import os
import unittest
import threading
from unittest.mock import patch
from types import SimpleNamespace

from app.services.llm.client import (
    call_llm,
    llm_operation_context,
    model_catalog,
    normalize_gemini_model_name,
    resolve_llm_config,
)
from app.services.llm.components import generate_component_code
from app.services.llm.errors import LLMBudgetError, LLMTimeoutError
from app.services.llm.parsing import (
    parse_llm_artifacts,
    parse_llm_document_insights,
    parse_llm_preprocessing_plan,
    parse_llm_text_map,
)
from app.services.llm.planning import generate_artifacts, generate_document_insights_from_pdf_file, generate_report_title
from app.services.llm.texts import generate_all_texts
from app.services.llm.prompts import prompt_fingerprint
from app.services.llm.refinement import parse_llm_refinement_decision, refine_session_modular
from app.services.llm.script import assemble_script
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER


class LlmContractTests(unittest.TestCase):
    def test_model_catalog_exposes_defaults_and_providers(self):
        catalog = model_catalog()

        self.assertEqual(catalog["default_provider"], DEFAULT_PROVIDER)
        self.assertEqual(catalog["default_model"], DEFAULT_MODEL_NAME)
        self.assertIn(DEFAULT_PROVIDER, catalog["providers"])
        self.assertTrue(catalog["providers"][DEFAULT_PROVIDER])

    def test_prompt_fingerprint_is_stable_sha256_metadata(self):
        fingerprint = prompt_fingerprint("GENERATE_ARTIFACTS.md")

        self.assertEqual(fingerprint["filename"], "GENERATE_ARTIFACTS.md")
        self.assertEqual(len(fingerprint["sha256"]), 64)
        self.assertTrue(fingerprint["version"])

    def test_gemini_model_names_are_new_sdk_compatible(self):
        catalog = model_catalog()

        self.assertEqual(normalize_gemini_model_name("models/gemini-2.5-flash"), "gemini-2.5-flash")
        self.assertTrue(all(
            not model["id"].startswith("models/")
            for model in catalog["providers"]["gemini"]
        ))

    def test_resolve_llm_config_rejects_unknown_provider_or_model(self):
        with self.assertRaises(ValueError):
            resolve_llm_config("missing", None)
        with self.assertRaises(ValueError):
            resolve_llm_config("deepseek", "missing-model")
        self.assertEqual(
            resolve_llm_config("gemini", "models/gemini-2.5-flash"),
            ("gemini", "gemini-2.5-flash"),
        )

    def test_generate_component_code_includes_visual_context(self):
        captured = {}

        def fake_call_llm(provider, model_name, system_instruction, user_prompt):
            captured["user_prompt"] = user_prompt
            return "def render_g_table(pdf, dataframes, x, y, w, h):\n    pass"

        with patch("app.services.llm.client.call_llm", fake_call_llm):
            code = generate_component_code(
                "[]",
                {
                    "id": "g_table",
                    "type": "table",
                    "indicator": "Top regions",
                    "component_preset_id": "table_summary",
                    "visual_options": {"top_n": 4},
                    "data_binding": {
                        "source": "sales.csv",
                        "roles": {"dimensions": "region", "metrics": "revenue"},
                        "aggregation": "sum",
                    },
                    "render_mode": "generated_code",
                },
                settings={"visual_theme_id": "minimal_mono", "chart_style_id": "dense_dashboard"},
            )

        self.assertIn("render_g_table", code)
        self.assertIn("Component Preset Contract", captured["user_prompt"])
        self.assertIn("table_summary", captured["user_prompt"])
        self.assertIn("Resolved Visual Theme", captured["user_prompt"])
        self.assertIn("minimal_mono", captured["user_prompt"])
        self.assertIn("Resolved Visual Options", captured["user_prompt"])
        self.assertIn("Data Binding Contract", captured["user_prompt"])
        self.assertIn("sales.csv", captured["user_prompt"])
        self.assertIn("Render Mode: generated_code", captured["user_prompt"])
        self.assertIn("Runtime Renderer API Reference", captured["user_prompt"])
        self.assertIn("render_summary_table", captured["user_prompt"])
        self.assertIn("render_runtime", captured["user_prompt"])

    def test_generate_report_title_sets_llm_operation_context(self):
        captured = {}

        def fake_call_llm(provider, model_name, system_instruction, user_prompt):
            from app.services.llm.client import current_llm_context

            captured["context"] = dict(current_llm_context())
            return "Título do relatório"

        with patch("app.services.llm.client.call_llm", fake_call_llm):
            title = generate_report_title(
                "[]",
                "Analisar vendas",
                [],
                settings={"language": "Portuguese"},
                job_id="job-123",
            )

        self.assertEqual(title, "Título do relatório")
        self.assertEqual(captured["context"]["operation"], "title_generation")
        self.assertEqual(captured["context"]["job_id"], "job-123")

    def test_narratives_receive_accepted_goal_and_plan_without_treating_plan_as_evidence(self):
        plan = [{
            "id": "g_delays", "type": "bar", "indicator": "Late deliveries by region",
            "data_binding": {"source": "shipments.csv", "roles": {"category": "region", "value": "delay_days"}},
            "code": "unused generated code",
        }]
        with patch("app.services.llm.client.call_llm", return_value='{"t1":"Focus on delivery reliability."}') as call:
            texts = generate_all_texts(
                "[]", [{"id": "t1", "indicator": "Recommendations"}], {"m_delay": "N/A"},
                report_goal="Prioritize delivery reliability for the operations director, not revenue.",
                report_artifacts=plan,
            )
        self.assertEqual(texts["t1"], "Focus on delivery reliability.")
        prompt = call.call_args.args[3]
        self.assertIn("Prioritize delivery reliability for the operations director, not revenue.", prompt)
        self.assertIn("Late deliveries by region", prompt)
        self.assertIn('"source": "shipments.csv"', prompt)
        self.assertIn("not calculated evidence", prompt)
        self.assertIn('"m_delay": "N/A"', prompt)
        self.assertNotIn("unused generated code", prompt)
        self.assertIn("never treat a planned chart", call.call_args.args[2])

    def test_planner_receives_candidate_relationships_with_join_caveats(self):
        semantic_model = {"relationships": [{"column_name": "region", "references": [
            {"file": "book.xlsx", "unique_values": 3, "rows": 20},
            {"file": "book.xlsx::targets", "unique_values": 3, "rows": 3},
        ]}]}
        with patch("app.services.llm.client.call_llm", return_value='[{"id":"t1","type":"text","indicator":"Summary"}]') as call:
            generate_artifacts("[]", "Compare sales with targets", semantic_model=semantic_model)
        prompt = call.call_args.args[3]
        self.assertIn("book.xlsx::targets", prompt)
        self.assertIn('"unique_values": 3', prompt)
        self.assertIn("not verified joins", prompt)
        self.assertIn("many-to-many", prompt)

    def test_generate_document_insights_from_pdf_uses_gemini_file_input(self):
        captured = {}
        pdf_path = "workspace/uploads/test_llm_document.pdf"
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n% fake test pdf\n")

        class FakeModels:
            @staticmethod
            def generate_content(*, model, contents, config):
                captured["model"] = model
                captured["contents"] = contents
                captured["config"] = config
                return SimpleNamespace(text='{"summary":"Resumo","metrics":[{"id":"m_total","label":"Total","value":42,"source":"brief.pdf","evidence":"Total 42"}]}')

        class FakeClient:
            models = FakeModels()

        try:
            with patch("app.services.llm.client.get_gemini_client", return_value=FakeClient()), \
                 patch("app.services.llm.client._document_pdf_provider", return_value=("gemini", "gemini-3.8-flash")):
                insights = generate_document_insights_from_pdf_file(
                    pdf_path,
                    '{"filename":"brief.pdf","type":"pdf","text_excerpt":""}',
                    "Extraia métricas",
                    provider="deepseek",
                    model_name="deepseek-v4-flash",
                    settings={"language": "Portuguese"},
                )
        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

        self.assertEqual(captured["model"], "gemini-3.8-flash")
        self.assertEqual(captured["config"]["http_options"]["timeout"], 45000)
        self.assertEqual(insights["metrics"][0]["id"], "m_total")
        self.assertEqual(insights["extraction_source"], "pdf_file")
        self.assertEqual(len(captured["contents"]), 2)

    def test_llm_schema_parsers_validate_core_payloads(self):
        artifacts = parse_llm_artifacts('[{"id": "g1", "indicator": "Chart", "type": "graphic", "columns": "a, b"}]')
        self.assertEqual(artifacts[0]["columns"], ["a", "b"])

        plan = parse_llm_preprocessing_plan('{"needs_filter": true, "description": "Filtro", "code": "def preprocess_dataframes(dataframes):\\n    return dataframes"}')
        self.assertTrue(plan["needs_preprocessing"])

        texts = parse_llm_text_map('{"t1": "Resumo", "empty": ""}')
        self.assertEqual(texts, {"t1": "Resumo"})

        document = parse_llm_document_insights('{"summary":"Resumo","metrics":[{"id":"m_budget","label":"Orçamento","value":10,"source":"brief.pdf","evidence":"Orçamento de 10"}]}')
        self.assertEqual(document["metrics"][0]["id"], "m_budget")
        self.assertEqual(document["metrics"][0]["confidence"], "low")

        decision = parse_llm_refinement_decision('{"intent":"clarify","scope":"report","instruction":"Melhorar","clarification_question":"Como deseja mudar?"}', {})
        self.assertEqual(decision["intent"], "clarify")

        with self.assertRaises(ValueError):
            parse_llm_artifacts('{"id": "not-a-list"}')

        with self.assertRaises(ValueError):
            parse_llm_refinement_decision("...", {})

    def test_assemble_script_imports_runtime_chart_helpers(self):
        script = assemble_script(
            {"g_table": "def render_g_table(pdf, dataframes, x, y, w, h):\n    pass"},
            [{"id": "g_table", "page": 1, "x": 10, "y": 20, "w": 80, "h": 50}],
            {},
            "workspace/reports/test.pdf",
            settings={"title": "Test", "visual_theme_id": "high_contrast", "chart_style_id": "minimal"},
        )

        self.assertIn("CHART_THEME", script)
        self.assertIn("import pdf_report_renderers.runtime as render_runtime", script)
        self.assertIn("from pdf_report_renderers.runtime import", script)
        self.assertIn("_render_table_image", script)
        self.assertIn("_top_n_with_others", script)
        self.assertNotIn("def render_gauge_chart", script)
        self.assertNotIn("def render_donut_chart", script)
        self.assertIn("high_contrast", script)

    def test_assemble_script_wraps_text_block_titles(self):
        script = assemble_script(
            {"t_long": "def render_t_long(pdf, dataframes, x, y, w, h):\n    render_text_block(pdf, 'Body', x, y, w, h, title='Very long title')"},
            [{"id": "t_long", "page": 1, "x": 10, "y": 20, "w": 80, "h": 50}],
            {},
            "workspace/reports/test.pdf",
            settings={"title": "Test", "font": "Arial", "font_size": 10, "alignment": "J", "line_height": 5},
        )

        self.assertIn("title_lines = _wrap_lines(title", script)
        self.assertIn("pdf.multi_cell(w, title_line_height", script)
        self.assertNotIn("pdf.cell(w, 7, title", script)

    def test_refinement_custom_visual_uses_generated_primary_with_runtime_reference(self):
        prompts = []

        def fake_call_llm(provider, model_name, system_instruction, user_prompt):
            prompts.append(user_prompt)
            if len(prompts) == 1:
                return '{"intent":"component","scope":"components","component_ids":["g_table"],"instruction":"Personalizar a tabela."}'
            return "def render_g_table(pdf, dataframes, x, y, w, h):\n    return render_summary_table(pdf, dataframes, x, y, w, h, dimensions=['region'], metrics=['revenue'])"

        session = {
            "job_id": "job-refine",
            "provider": DEFAULT_PROVIDER,
            "model_name": DEFAULT_MODEL_NAME,
            "settings": {"language": "Portuguese"},
            "artifacts": [
                {
                    "id": "g_table",
                    "indicator": "Top regions",
                    "type": "table",
                    "component_preset_id": "table_summary",
                    "render_mode": "library",
                }
            ],
            "components": {},
            "files": [{"artifact": {"filename": "sales.csv", "columns": ["region", "revenue"]}}],
        }

        with patch("app.services.llm.client.call_llm", fake_call_llm):
            updated = refine_session_modular(session, "customize a tabela", job_id="job-refine")

        self.assertEqual(updated["artifacts"][0]["render_mode"], "generated_code")
        self.assertIn("render_summary_table", updated["components"]["g_table"])
        self.assertTrue(updated["needs_script_update"])
        self.assertIn("Runtime Renderer API Reference", prompts[1])
        self.assertIn("primary renderer", prompts[1])

    def test_refinement_color_change_updates_visual_options_without_generated_code(self):
        session = {
            "job_id": "job-refine-color",
            "provider": DEFAULT_PROVIDER,
            "model_name": DEFAULT_MODEL_NAME,
            "settings": {"language": "Portuguese"},
            "artifacts": [
                {
                    "id": "g_cases_trend",
                    "indicator": "Tendência Temporal de Casos",
                    "type": "graphic",
                    "component_preset_id": "line_timeseries",
                    "render_mode": "library",
                    "visual_options": {},
                }
            ],
            "components": {"g_cases_trend": "def render_g_cases_trend(pdf, dataframes, x, y, w, h):\n    pass"},
            "files": [{"artifact": {"filename": "cases.csv", "columns": []}}],
        }

        calls = []

        def fake_call_llm(*_args, **_kwargs):
            calls.append(True)
            return '{"intent":"visual","scope":"components","component_ids":["g_cases_trend"],"instruction":"Trocar a linha de azul para vermelho.","visual_options":{"line_color":"#dc2626"}}'

        with patch("app.services.llm.client.call_llm", fake_call_llm):
            updated = refine_session_modular(
                session,
                'Troque a cor do gráfico "Tendência Temporal de Casos" de azul para vermelho',
                job_id="job-refine-color",
            )

        artifact = updated["artifacts"][0]
        self.assertEqual(len(calls), 1)
        self.assertEqual(artifact["render_mode"], "library")
        self.assertEqual(artifact["visual_options"]["line_color"], "#dc2626")
        self.assertNotIn("g_cases_trend", updated["components"])
        self.assertTrue(updated["needs_script_update"])

    def test_call_llm_enforces_budget(self):
        class FakeDeepseekClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(*args, **kwargs):
                        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

        with patch("app.services.llm.client.get_deepseek_client", return_value=FakeDeepseekClient()), \
             patch("app.services.llm.client.record_llm_usage") as record_usage:
            with llm_operation_context(job_id="job-budget", operation="budget_test", settings={"llm_call_budget": 1}):
                self.assertEqual(call_llm("deepseek", "deepseek-v4-flash", "sys", "user"), "ok")
                with self.assertRaises(LLMBudgetError):
                    call_llm("deepseek", "deepseek-v4-flash", "sys", "user")

        self.assertEqual(record_usage.call_count, 1)

    def test_call_llm_times_out_after_retries(self):
        class FakeDeepseekClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(*args, **kwargs):
                        raise TimeoutError("timed out")

        with patch("app.services.llm.client.get_deepseek_client", return_value=FakeDeepseekClient()), \
             patch("app.services.llm.client.record_llm_usage"):
            with llm_operation_context(job_id="job-timeout", operation="timeout_test", settings={"llm_timeout_seconds": 1}):
                with self.assertRaises(LLMTimeoutError):
                    call_llm("deepseek", "deepseek-v4-flash", "sys", "user", retries=1)

    def test_call_llm_does_not_install_signal_timeout_in_background_thread(self):
        class FakeDeepseekClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(*args, **kwargs):
                        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

        result = {}

        def worker():
            try:
                with llm_operation_context(job_id="job-thread", operation="thread_test", settings={"llm_timeout_seconds": 1}):
                    result["value"] = call_llm("deepseek", "deepseek-v4-flash", "sys", "user", retries=1)
            except Exception as exc:
                result["error"] = exc

        with patch("app.services.llm.client.get_deepseek_client", return_value=FakeDeepseekClient()), \
             patch("app.services.llm.client.record_llm_usage"):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", result)
        self.assertEqual(result["value"], "ok")

    def test_call_llm_uses_configured_fallback_provider(self):
        class FailingDeepseekClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(*args, **kwargs):
                        raise RuntimeError("deepseek unavailable")

        class FakeGeminiResponse:
            text = "fallback result"

        class FakeGeminiClient:
            class models:
                @staticmethod
                def generate_content(*args, **kwargs):
                    return FakeGeminiResponse()

        with patch("app.services.llm.client.get_deepseek_client", return_value=FailingDeepseekClient()), \
             patch("app.services.llm.client.get_gemini_client", return_value=FakeGeminiClient()), \
             patch("app.services.llm.client.operation_preferences", return_value={
                 "llm_fallback_provider": "gemini", "llm_fallback_model_name": "gemini-2.5-flash",
             }), \
             patch("app.services.llm.client.resolve_api_key", return_value="fake-owned-key"), \
             patch("app.services.llm.client.record_llm_usage"):
            with llm_operation_context(
                job_id="job-fallback",
                operation="fallback_test",
            ):
                self.assertEqual(
                    call_llm("deepseek", "deepseek-v4-flash", "sys", "user", retries=1),
                    "fallback result",
                )


if __name__ == "__main__":
    unittest.main()
