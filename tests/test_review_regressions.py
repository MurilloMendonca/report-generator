import asyncio
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import pandas as pd

import app.main as main
import app.routers.api as api
import app.security as security
import app.services.pipeline_service as pipeline_service
import app.services.session_manager as session_manager
import app.services.web_helpers as web_helpers
from app.models.session import normalize_session
from app.services.executor_service import execute_script
from app.services.llm.client import call_llm, llm_operation_context, resolve_llm_config
from app.services.llm.errors import LLMBudgetError
from app.services.llm.metrics import calculate_metric_value, dataframe_load_line
from app.services.llm.parsing import parse_llm_text_map
from app.services.llm.planning import generate_artifacts
from app.services.llm.texts import generate_all_texts
from app.services.pipeline_service import apply_data_filter
from app.utils.artifact_validation import column_exists, metadata_columns, validate_report_plan
from app.utils.component_renderers import sandbox_runtime_source
from app.utils.data_analyzer import extract_metadata
from app.utils.tabular import load_tabular_dataframes


class TabularRegressionTests(unittest.TestCase):
    def setUp(self):
        os.makedirs("workspace/uploads", exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="review_", dir="workspace/uploads")
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_csv_profile_and_runtime_agree_on_locale_and_quoted_path(self):
        path = self.root / "seller's.csv"
        path.write_bytes("região;receita\nSul;10,5\nNorte;20,0\n".encode("cp1252"))
        metadata = extract_metadata(str(path), "sales.csv")
        self.assertEqual(metadata["parser"]["separator"], ";")
        self.assertEqual(metadata["columns"][1]["stats"]["mean"], 15.25)
        script = "import json\ndataframes = {}\n" + dataframe_load_line("sales.csv", str(path.resolve()))
        script += "\nprint(json.dumps(dataframes['sales.csv']['receita'].sum().item()))"
        result = execute_script(script, str(self.root / "load.py"))
        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(json.loads(result["stdout"]), 30.5)

    def test_metric_retry_receives_execution_error_and_runs_shared_loader(self):
        path = self.root / "sales.csv"
        path.write_text("region;sales\nA;10\nB;20\n", encoding="utf-8")
        with patch("app.services.llm.client.call_llm", side_effect=[
            "raise ValueError('wrong calculation')",
            "print(json.dumps({'value': int(dataframes['sales.csv']['sales'].sum())}))",
        ]) as call, patch("app.services.llm.metrics.time.sleep"):
            value = calculate_metric_value("[]", {"id": "m_sales"}, {"sales.csv": str(path)})
        self.assertEqual(value, 30)
        self.assertIn("wrong calculation", call.call_args.args[3])

    def test_profiles_all_rows_and_serializes_excel_dates(self):
        path = self.root / "large.csv"
        pd.DataFrame({"value": range(10005)}).to_csv(path, index=False)
        metadata = extract_metadata(str(path))
        self.assertEqual(metadata["shape"]["rows"], 10005)
        self.assertEqual(metadata["columns"][0]["stats"]["max"], 10004)
        path = self.root / "dates.xlsx"
        pd.DataFrame({"date": [pd.Timestamp("2026-10-01")], "paid": [3], "candidate": [4]}).to_excel(path, index=False)
        metadata = extract_metadata(str(path))
        json.dumps(metadata, allow_nan=False)
        self.assertEqual(metadata["columns"][1]["role"], "measure")
        self.assertEqual(metadata["columns"][2]["role"], "measure")
        self.assertTrue(metadata["sample"][0]["date"].startswith("2026-10-01"))

    def test_excel_numeric_headers_match_prompt_column_names(self):
        path = self.root / "years.xlsx"
        pd.DataFrame({2026: [10]}).to_excel(path, index=False)
        self.assertEqual(extract_metadata(str(path))["columns"][0]["name"], "2026")
        tables = load_tabular_dataframes("years.xlsx", str(path))
        self.assertEqual(tables["years.xlsx"]["2026"].iloc[0], 10)

    def test_second_sheet_is_loaded_validated_and_preserved_by_preprocessing(self):
        path = self.root / "book.xlsx"
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame({"region": ["A"], "sales": [10]}).to_excel(writer, sheet_name="sales", index=False)
            pd.DataFrame({"region": ["A"], "target": [12]}).to_excel(writer, sheet_name="targets", index=False)
        metadata = extract_metadata(str(path), "book.xlsx")
        tables = load_tabular_dataframes("book.xlsx", str(path))
        self.assertEqual(tables["book.xlsx::targets"]["target"].iloc[0], 12)
        self.assertEqual(metadata["sheets"][1]["dataframe_key"], "book.xlsx::targets")
        available = metadata_columns([metadata])
        self.assertTrue(column_exists("book.xlsx::targets:target", available))
        self.assertFalse(column_exists("missing.csv:target", available))
        _artifacts, issues = validate_report_plan([{
            "id": "g_target", "type": "bar", "indicator": "Targets",
            "component_preset_id": "bar_ranked", "render_mode": "library",
            "columns": ["book.xlsx::targets:region", "book.xlsx::targets:target"],
            "data_binding": {"source": "book.xlsx::targets", "roles": {"category": "region", "value": "target"}},
        }], [metadata])
        self.assertFalse([issue for issue in issues if issue["code"].startswith("data_binding")], issues)
        files = [{"original_filename": "book.xlsx", "path": str(path), "raw_artifact": metadata, "artifact": metadata}]
        plan = {"description": "Double targets", "code": (
            "def preprocess_dataframes(dataframes):\n"
            "    dataframes['book.xlsx::targets']['target'] *= 2\n"
            "    return dataframes\n"
        )}
        job_id = f"review-{uuid.uuid4().hex}"
        script_path = Path("workspace/scripts") / f"preprocess_{job_id}.py"
        self.addCleanup(script_path.unlink, missing_ok=True)
        output = apply_data_filter(job_id, {"files": files}, plan)
        output_path = Path(output[0]["preprocessed_path"])
        self.addCleanup(output_path.unlink, missing_ok=True)
        processed = pd.read_excel(output_path, sheet_name=None)
        self.assertEqual(list(processed), ["sales", "targets"])
        self.assertEqual(processed["targets"]["target"].iloc[0], 24)
        self.assertEqual(processed["sales"]["sales"].iloc[0], 10)

    def test_renderer_does_not_substitute_an_unrelated_source(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        with self.assertRaisesRegex(ValueError, "Data source not found"):
            namespace["_library_first_dataframe"]({"a.csv": pd.DataFrame({"value": [5]})}, "missing.csv")

    def test_gauge_requires_real_target_and_preserves_overachievement(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        namespace["_library_empty_chart"] = Mock()
        namespace["_save_chart_to_pdf"] = Mock()
        tables = {"sales.csv": pd.DataFrame({"sales": [25]})}
        namespace["render_gauge_chart"](object(), tables, 0, 0, 90, 60, value="sales", source="sales.csv")
        namespace["_library_empty_chart"].assert_called_once()
        namespace["_save_chart_to_pdf"].assert_not_called()
        namespace["render_gauge_chart"](object(), tables, 0, 0, 90, 60, value="sales", source="sales.csv", options={"target": 20})
        figure = namespace["_save_chart_to_pdf"].call_args.args[0]
        self.addCleanup(namespace["plt"].close, figure)
        self.assertIn("125%", [text.get_text() for text in figure.axes[0].texts])

    def test_filter_failures_cannot_silently_expand_report_population(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        df = pd.DataFrame({"name": ["A[1]", "A1"]})
        with self.assertRaisesRegex(ValueError, "Filter column not found"):
            namespace["_library_apply_filters"](df, [{"column": "missing", "value": 1}])
        filtered = namespace["_library_apply_filters"](df, [{"column": "name", "operator": "contains", "value": "[1]"}])
        self.assertEqual(filtered["name"].tolist(), ["A[1]"])


class LlmReliabilityRegressionTests(unittest.TestCase):
    def test_json_parser_ignores_text_after_complete_payload(self):
        self.assertEqual(parse_llm_text_map('Result: {"t1":"Text with } and [ characters"} Extra: {"other":1}'), {"t1": "Text with } and [ characters"})

    def client(self, side_effect=None):
        client = Mock()
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")])
        client.chat.completions.create.side_effect = side_effect
        client.chat.completions.create.return_value = response
        return client

    def test_budget_limits_retries_and_nested_operations(self):
        error = RuntimeError("Service unavailable")
        error.status_code = 503
        client = self.client(error)
        with patch("app.services.llm.client.get_deepseek_client", return_value=client):
            with llm_operation_context(settings={"llm_call_budget": 1}):
                with self.assertRaises(LLMBudgetError):
                    call_llm("deepseek", None, "system", "user", delay=0)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        client = self.client()
        with patch("app.services.llm.client.get_deepseek_client", return_value=client):
            with llm_operation_context(settings={"llm_call_budget": 1}):
                with llm_operation_context(operation="child"):
                    call_llm("deepseek", None, "system", "user")
                with self.assertRaises(LLMBudgetError):
                    call_llm("deepseek", None, "system", "user")

    def test_gemini_background_request_has_http_timeout_and_system_instruction(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text="ok")
        result = []
        def worker():
            with llm_operation_context(settings={"llm_timeout_seconds": 7}):
                result.append(call_llm("gemini", None, "system", "user"))
        with patch("app.services.llm.client.get_gemini_client", return_value=client):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=5)
        self.assertEqual(result, ["ok"])
        arguments = client.models.generate_content.call_args.kwargs
        self.assertEqual(arguments["config"]["http_options"]["timeout"], 7000)
        self.assertEqual(arguments["config"]["system_instruction"], "system")
        self.assertEqual(arguments["contents"], "user")
        client.close.assert_called_once()

    def test_provider_failure_is_not_retried_by_contract_parser(self):
        with patch("app.services.llm.client.call_llm", side_effect=ValueError("Missing key")) as call:
            with self.assertRaisesRegex(ValueError, "Missing key"):
                generate_artifacts("[]", "Report")
        self.assertEqual(call.call_count, 1)

    def test_missing_narrative_is_rejected_and_retry_gets_feedback(self):
        with patch("app.services.llm.client.call_llm", side_effect=['{}', '{"t1":"Evidence-based summary"}']) as call:
            texts = generate_all_texts("[]", [{"id": "t1"}], {})
        self.assertEqual(texts["t1"], "Evidence-based summary")
        self.assertIn("Missing text for components: t1", call.call_args.args[3])

    def test_legacy_session_models_migrate_to_supported_endpoints(self):
        self.assertEqual(resolve_llm_config("deepseek", "deepseek-v4-flash"), ("deepseek", "deepseek-flash"))
        session = normalize_session({"provider": "gemini", "model_name": "gemini-3.1-flash-lite-preview"})
        self.assertEqual(session["model_name"], "gemini-3.1-flash-lite")
        self.assertEqual(normalize_session({"provider": "gemini"})["model_name"], "gemini-3.8-flash")


class ApiRegressionTests(unittest.TestCase):
    def test_http_authentication_and_provider_default_model(self):
        from fastapi.testclient import TestClient
        from tests.auth_helpers import TEST_PASSWORD, csrf_headers, make_user
        user = make_user()
        with TestClient(main.app) as client:
            self.assertEqual(client.get("/api/models").status_code, 401)
            anonymous = csrf_headers()
            self.assertEqual(client.post("/api/auth/login", json={"email": user.email, "password": []}, headers=anonymous).status_code, 422)
            login = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}, headers=anonymous)
            self.assertEqual(login.status_code, 200, login.text)
            token = login.cookies.get("pdf_report_session")
            headers = {"Cookie": f"pdf_report_session={token}; {anonymous['Cookie']}", "X-CSRF-Token": anonymous["X-CSRF-Token"]}
            catalog = client.get("/api/models", headers=headers).json()
            self.assertEqual(catalog["default_model"], "deepseek-flash")
            with patch.object(api, "save_uploaded_files", return_value=[]), \
                 patch.object(api, "create_session") as create, patch.object(api, "run_pipeline"):
                response = client.post("/api/sessions", data={"provider": "gemini"}, files={"files": ("data.csv", b"a\n1\n", "text/csv")}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(create.call_args.args[1]["model_name"], "gemini-3.8-flash")
            self.assertEqual(create.call_args.kwargs["owner_id"], user.id)
            self.assertEqual(client.post("/api/auth/logout", headers=headers).status_code, 200)
            self.assertEqual(client.get("/api/models", headers=headers).status_code, 401)

    def test_upload_batch_failure_cleans_all_new_files(self):
        import io
        from fastapi import HTTPException, UploadFile
        for module in (web_helpers,):
            with tempfile.TemporaryDirectory(dir="workspace/uploads") as directory:
                with patch.object(module, "UPLOAD_DIR", directory):
                    files = [UploadFile(filename="good.csv", file=io.BytesIO(b"a,b\n1,2\n")),
                             UploadFile(filename="bad.pdf", file=io.BytesIO(b"invalid"))]
                    with self.assertRaises(HTTPException):
                        module.save_uploaded_files(files)
                self.assertEqual(os.listdir(directory), [])

    def test_malformed_legacy_excel_returns_validation_error(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory(dir="workspace/uploads") as directory:
            path = Path(directory) / "broken.xls"
            path.write_bytes(b"not an Excel workbook")
            with self.assertRaises(HTTPException) as error:
                web_helpers.validate_saved_upload(str(path), ".xls")
            self.assertEqual(error.exception.status_code, 400)

    def test_exploration_work_runs_outside_event_loop_thread(self):
        event_loop_thread = threading.get_ident()
        worker_threads = []
        def generate(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            return {"answer": "answer", "turn_id": "t"}
        from fastapi import BackgroundTasks
        with patch.object(api, "require_session", return_value={"files": []}), \
             patch.object(api, "start_exploration", side_effect=generate):
            self.assertEqual(asyncio.run(api.explore_data("test", BackgroundTasks(), "Question")), {"answer": "answer", "turn_id": "t"})
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads, [event_loop_thread])


if __name__ == "__main__":
    unittest.main()
