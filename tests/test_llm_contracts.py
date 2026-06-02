import unittest
from unittest.mock import patch

from app.services.llm_service import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    assemble_script,
    generate_component_code,
    model_catalog,
    normalize_gemini_model_name,
    parse_llm_artifacts,
    parse_llm_preprocessing_plan,
    parse_llm_text_map,
    prompt_fingerprint,
    resolve_llm_config,
)


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

        with patch("app.services.llm_service.call_llm", fake_call_llm):
            code = generate_component_code(
                "[]",
                {
                    "id": "g_table",
                    "type": "table",
                    "indicator": "Top regions",
                    "component_preset_id": "table_summary",
                    "visual_options": {"top_n": 4},
                },
                settings={"visual_theme_id": "minimal_mono", "chart_style_id": "dense_dashboard"},
            )

        self.assertIn("render_g_table", code)
        self.assertIn("Component Preset Contract", captured["user_prompt"])
        self.assertIn("table_summary", captured["user_prompt"])
        self.assertIn("Resolved Visual Theme", captured["user_prompt"])
        self.assertIn("minimal_mono", captured["user_prompt"])
        self.assertIn("Resolved Visual Options", captured["user_prompt"])

    def test_llm_schema_parsers_validate_core_payloads(self):
        artifacts = parse_llm_artifacts('[{"id": "g1", "indicator": "Chart", "type": "graphic", "columns": "a, b"}]')
        self.assertEqual(artifacts[0]["columns"], ["a", "b"])

        plan = parse_llm_preprocessing_plan('{"needs_filter": true, "description": "Filtro", "code": "def preprocess_dataframes(dataframes):\\n    return dataframes"}')
        self.assertTrue(plan["needs_preprocessing"])

        texts = parse_llm_text_map('{"t1": "Resumo", "empty": ""}')
        self.assertEqual(texts, {"t1": "Resumo"})

        with self.assertRaises(ValueError):
            parse_llm_artifacts('{"id": "not-a-list"}')

    def test_assemble_script_injects_chart_theme_helpers(self):
        script = assemble_script(
            {"g_table": "def render_g_table(pdf, dataframes, x, y, w, h):\n    pass"},
            [{"id": "g_table", "page": 1, "x": 10, "y": 20, "w": 80, "h": 50}],
            {},
            "workspace/reports/test.pdf",
            settings={"title": "Test", "visual_theme_id": "high_contrast", "chart_style_id": "minimal"},
        )

        self.assertIn("CHART_THEME", script)
        self.assertIn("_render_table_image", script)
        self.assertIn("_top_n_with_others", script)
        self.assertIn("high_contrast", script)


if __name__ == "__main__":
    unittest.main()
