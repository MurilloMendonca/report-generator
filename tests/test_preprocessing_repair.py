"""A failing preprocessing script is repaired by the model a bounded number of times."""
import json
import unittest
import uuid
from unittest.mock import patch

import pandas as pd

from app.services import pipeline_service
from app.services.cleanup_service import delete_entire_session
from app.services.executor_service import probe_bubblewrap
from app.services.llm.errors import LLMContractError
from app.services.llm.planning import fix_data_filter, normalize_preprocessing_payload
from app.services.pipeline_service import PreprocessingScriptError, apply_data_filter_with_repairs, run_pipeline
from app.utils.report_paths import ReportPaths
from app.services.report_quality import review_report
from app.services.session_manager import get_session, reserve_session_run
from tests.auth_helpers import create_session, default_user


BROKEN = (
    "def preprocess_dataframes(dataframes):\n"
    "    df = dataframes['Sheet1'].copy()\n"
    "    return {'sales.csv': df[df['channel'] != 'Partner']}\n"
)
FIXED = (
    "def preprocess_dataframes(dataframes):\n"
    "    processed = {name: df.copy() for name, df in dataframes.items()}\n"
    "    df = processed['sales.csv']\n"
    "    processed['sales.csv'] = df[df['channel'] != 'Partner']\n"
    "    return processed\n"
)
PLAN = {"needs_preprocessing": True, "description": "Remove Partner rows", "code": BROKEN}

BWRAP_AVAILABLE, BWRAP_ERROR = probe_bubblewrap()


class FixDataFilterContractTests(unittest.TestCase):
    def test_prompt_lists_valid_keys_and_the_end_of_the_traceback(self):
        error = "x" * 6000 + "\nKeyError: 'Sheet1'"
        with patch("app.services.llm.client.call_llm", return_value=f"```python\n{FIXED}```") as llm:
            code = fix_data_filter(BROKEN, error, "{}", ["sales.csv", "targets.xlsx::Q2"], prompt="Exclude Partner",
                                   columns={"sales.csv": ["region", "channel"]})
        self.assertEqual(code, FIXED.strip())
        system, prompt = llm.call_args.args[2], llm.call_args.args[3]
        self.assertIn("Data Preprocessing Repair", system)
        self.assertIn('["sales.csv", "targets.xlsx::Q2"]', prompt)
        self.assertIn("KeyError: 'Sheet1'", prompt)
        self.assertNotIn("x" * 4500, prompt)
        self.assertIn('{"sales.csv": ["region", "channel"]}', prompt)

    def test_answers_without_the_function_are_rejected_after_one_retry(self):
        with patch("app.services.llm.client.call_llm", return_value="I fixed it.") as llm:
            with self.assertRaises(LLMContractError):
                fix_data_filter(BROKEN, "KeyError", "{}", ["sales.csv"])
        self.assertEqual(llm.call_count, 2)

    def test_plans_without_the_flag_count_as_requested(self):
        self.assertTrue(normalize_preprocessing_payload(PLAN)["requested_by_user"])
        unrequested = normalize_preprocessing_payload({**PLAN, "requested_by_user": False})
        self.assertFalse(unrequested["requested_by_user"])
        self.assertTrue(unrequested["needs_preprocessing"])


@unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
class PreprocessingRepairTests(unittest.TestCase):
    def make_session(self, plan=None):
        job_id = f"test-preproc-fix-{uuid.uuid4().hex}"
        paths = ReportPaths.for_new_report(job_id, str(default_user().id)).ensure()
        csv = paths.uploads / "sales.csv"
        csv.write_text("region,channel,revenue\nNorth,Direct,10\nSouth,Partner,20\nWest,Direct,5\n", encoding="utf-8")
        workbook = paths.uploads / "targets.xlsx"
        with pd.ExcelWriter(workbook) as writer:
            pd.DataFrame({"region": ["North"], "target": [12]}).to_excel(writer, sheet_name="Q1", index=False)
            pd.DataFrame({"region": ["South"], "target": [18]}).to_excel(writer, sheet_name="Q2", index=False)
        files = [{"original_filename": "sales.csv", "path": str(csv)},
                 {"original_filename": "targets.xlsx", "path": str(workbook)}]
        create_session(job_id, {"files": files, "initial_prompt": "Exclude Partner rows",
                                "provider": "deepseek", "model_name": "deepseek-flash",
                                "data_preprocessing": dict(plan or PLAN), **paths.session_fields()})
        self.addCleanup(delete_entire_session, job_id)
        return job_id, reserve_session_run(job_id)["run_id"]

    def test_failure_reports_every_dataframe_key(self):
        job_id, _ = self.make_session()
        with self.assertRaises(PreprocessingScriptError) as raised:
            pipeline_service.apply_data_filter(job_id, get_session(job_id), dict(PLAN))
        self.assertIn("KeyError: 'Sheet1'", raised.exception.stderr)
        self.assertEqual(raised.exception.dataframe_keys, ["sales.csv", "targets.xlsx", "targets.xlsx::Q2"])

    def test_columns_are_listed_per_dataframe_key(self):
        job_id, _ = self.make_session()
        session = get_session(job_id)
        files = session["files"]
        for file_info in files:
            file_info["raw_artifact"] = pipeline_service.extract_metadata(file_info["path"], file_info["original_filename"])
        columns = pipeline_service.dataframe_columns({"files": files})
        self.assertEqual(columns, {"sales.csv": ["region", "channel", "revenue"],
                                   "targets.xlsx": ["region", "target"], "targets.xlsx::Q2": ["region", "target"]})

    def test_repaired_code_runs_and_is_saved(self):
        job_id, run_id = self.make_session()
        plan = dict(PLAN)
        with patch("app.services.llm.client.call_llm", return_value=FIXED) as llm:
            files = apply_data_filter_with_repairs(job_id, run_id, get_session(job_id), plan, "Exclude Partner rows")
        self.assertEqual(llm.call_count, 1)
        self.assertIn('"targets.xlsx::Q2"', llm.call_args.args[3])
        processed = pd.read_csv(files[0]["preprocessed_path"])
        self.assertEqual(processed["channel"].tolist(), ["Direct", "Direct"])
        session = get_session(job_id)
        self.assertEqual(session["data_preprocessing"]["code"], FIXED.strip())
        self.assertTrue(any("solicitando correção" in str(log) for log in session["logs"]))

    def test_requested_preprocessing_is_dropped_with_a_review_warning_after_the_limit(self):
        job_id, run_id = self.make_session()
        plan = dict(PLAN)
        with patch("app.services.llm.client.call_llm", return_value=BROKEN) as llm:
            files = apply_data_filter_with_repairs(job_id, run_id, get_session(job_id), plan, "Exclude Partner rows")
        self.assertEqual(llm.call_count, pipeline_service.PREPROCESSING_FIX_ATTEMPTS)
        self.assertFalse(any(f.get("preprocessed_path") for f in files))
        self.assertTrue(plan["discarded"]["requested_by_user"])
        review = review_report({"data_preprocessing": plan}, "/missing.pdf", {"components": []})
        warning = next(item for item in review["issues"] if item["code"] == "preprocessing_discarded")
        self.assertEqual(warning["severity"], "warning")
        self.assertIn("pedido", warning["message"])
        self.assertIn("Remove Partner rows", warning["message"])

    def test_unrequested_preprocessing_is_dropped_after_the_last_repair(self):
        job_id, run_id = self.make_session()
        plan = {**PLAN, "requested_by_user": False}
        with patch("app.services.llm.client.call_llm", return_value=BROKEN) as llm:
            files = apply_data_filter_with_repairs(job_id, run_id, get_session(job_id), plan, "Revenue by region")
        self.assertEqual(llm.call_count, pipeline_service.PREPROCESSING_FIX_ATTEMPTS)
        self.assertFalse(any(f.get("preprocessed_path") for f in files))
        self.assertFalse(pipeline_service.preprocessing_needed(plan))
        self.assertTrue(plan["evaluated"])
        self.assertIn("KeyError", plan["discarded"]["error"])
        logs = get_session(job_id)["logs"]
        self.assertTrue(any("seguindo com os dados originais" in str(log) for log in logs))

    def test_pipeline_continues_with_original_data_when_unrequested_preprocessing_fails(self):
        job_id, _ = self.make_session(plan={})
        plan = [{"id": "kpi_resumo", "type": "text", "indicator": "Summary", "page": 1},
                {"id": "kpi_conclusao", "type": "text", "indicator": "Conclusion", "page": 1}]

        def fake_llm(provider, model_name, system, prompt, **kwargs):
            if "Data Preprocessing Planner" in system:
                return json.dumps({**PLAN, "requested_by_user": False})
            if "Data Preprocessing Repair" in system:
                return BROKEN
            if "Report Planning & Indicator Selection" in system:
                return json.dumps(plan)
            if "Executive Copywriter" in system:
                return json.dumps({"kpi_resumo": "All channels.", "kpi_conclusao": "Keep going."})
            if "title" in system.lower():
                return "Sales"
            self.fail(f"Unexpected model call: {system[:80]}")

        with patch("app.services.llm.client.call_llm", side_effect=fake_llm):
            run_pipeline(job_id)
        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed", session.get("error"))
        self.assertFalse(any(f.get("preprocessed_path") for f in session["files"]))
        self.assertIn("discarded", session["data_preprocessing"])

    def test_document_insights_survive_preprocessing(self):
        # Losing them made every pipeline restart extract the PDF again and replan the whole report.
        job_id, _ = self.make_session()
        session = get_session(job_id)
        notes = ReportPaths.from_session(session).uploads / "notes.txt"
        notes.write_text("Partner sales are excluded from targets.", encoding="utf-8")
        document = {"type": "text", "filename": "notes.txt", "document_insights": {"summary": "Partner excluded"}}
        files = session["files"] + [{"original_filename": "notes.txt", "path": str(notes),
                                     "raw_artifact": {"type": "text", "filename": "notes.txt"}, "artifact": document}]
        for file_info in files[:2]:
            file_info["raw_artifact"] = {"filename": file_info["original_filename"]}
        session = pipeline_service.update_session(job_id, {"files": files})
        files = pipeline_service.apply_data_filter(job_id, session, {**PLAN, "code": FIXED})
        self.assertEqual(files[2]["artifact"]["document_insights"], {"summary": "Partner excluded"})
        self.assertTrue(files[0].get("preprocessed_path"))
        cleared = pipeline_service.clear_data_filter_state(files)
        self.assertEqual(cleared[2]["artifact"]["document_insights"], {"summary": "Partner excluded"})
        self.assertEqual(cleared[0]["artifact"], {"filename": "sales.csv"})

    def test_obsolete_runs_do_not_ask_for_repairs(self):
        job_id, run_id = self.make_session()
        reserve_session_run(job_id)  # A newer run replaces this one.
        with patch("app.services.llm.client.call_llm") as llm:
            self.assertIsNone(apply_data_filter_with_repairs(job_id, run_id, get_session(job_id), dict(PLAN), "goal"))
        llm.assert_not_called()

    def test_pipeline_completes_after_a_repaired_preprocessing_script(self):
        job_id, _ = self.make_session(plan={})
        plan = [{"id": "kpi_resumo", "type": "text", "indicator": "Summary", "page": 1},
                {"id": "kpi_conclusao", "type": "text", "indicator": "Conclusion", "page": 1}]

        def fake_llm(provider, model_name, system, prompt, **kwargs):
            if "Data Preprocessing Planner" in system:
                return json.dumps(PLAN)
            if "Data Preprocessing Repair" in system:
                return FIXED
            if "Report Planning & Indicator Selection" in system:
                return json.dumps(plan)
            if "Executive Copywriter" in system:
                return json.dumps({"kpi_resumo": "Direct sales lead.", "kpi_conclusao": "Keep Direct focus."})
            if "title" in system.lower():
                return "Direct sales"
            self.fail(f"Unexpected model call: {system[:80]}")

        with patch("app.services.llm.client.call_llm", side_effect=fake_llm):
            run_pipeline(job_id)
        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed", session.get("error"))
        processed = pd.read_csv(session["files"][0]["preprocessed_path"])
        self.assertNotIn("Partner", processed["channel"].tolist())


if __name__ == "__main__":
    unittest.main()
