import json
import os
import unittest
import uuid

import pandas as pd

from app.services.executor_service import execute_script
from app.services.llm_service import assemble_script
from app.utils.component_library import list_presets
from app.utils.component_renderers import (
    library_component_code,
    rename_generated_component_code,
    runtime_api_prompt_reference,
    sandbox_runtime_source,
    supports_library_renderer,
)


class ComponentRendererTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []
        os.makedirs("workspace/uploads", exist_ok=True)
        os.makedirs("workspace/reports", exist_ok=True)
        os.makedirs("workspace/scripts", exist_ok=True)

    def tearDown(self):
        for path in self.created_paths:
            if os.path.exists(path):
                os.remove(path)

    def tracked_path(self, path):
        self.created_paths.append(path)
        return path

    def test_library_metadata_exposes_supported_render_modes(self):
        presets = {preset["id"]: preset for preset in list_presets()}

        self.assertTrue(supports_library_renderer("bar_ranked"))
        self.assertIn("library", presets["bar_ranked"]["supported_render_modes"])
        self.assertIn("hybrid", presets["table_summary"]["supported_render_modes"])

    def test_library_component_code_wraps_visual_renderer(self):
        code = library_component_code({
            "id": "g_sales",
            "component_preset_id": "bar_ranked",
            "render_mode": "library",
        })

        self.assertIn("def render_g_sales", code)
        self.assertIn("render_visual_component", code)
        self.assertIn("json.loads", code)
        self.assertIn('\\"render_mode\\": \\"library\\"', code)

    def test_renames_generated_code_for_hybrid_fallback(self):
        code = "def render_g_sales(pdf, dataframes, x, y, w, h):\n    pass"

        renamed = rename_generated_component_code("g_sales", code)

        self.assertIn("def render_g_sales_generated", renamed)
        self.assertNotIn("def render_g_sales(pdf", renamed)

    def test_runtime_api_reference_supports_concise_customization(self):
        reference = runtime_api_prompt_reference("table_summary")

        self.assertIn("render_summary_table", reference)
        self.assertIn("render_runtime", reference)
        self.assertIn("_library_dataframe", reference)
        self.assertIn("Do not paste runtime helper bodies", reference)

    def test_assemble_script_executes_library_bar_renderer(self):
        csv_path = self.tracked_path(f"workspace/uploads/library_renderer_{uuid.uuid4().hex}.csv")
        pd.DataFrame({
            "region": ["Norte", "Sul", "Norte", "Leste"],
            "revenue": [100, 60, 40, 30],
        }).to_csv(csv_path, index=False)
        output_pdf = os.path.abspath(self.tracked_path(f"workspace/reports/library_renderer_{uuid.uuid4().hex}.pdf"))
        self.created_paths.append(f"{output_pdf}.manifest.json")
        script_path = self.tracked_path(f"workspace/scripts/library_renderer_{uuid.uuid4().hex}.py")

        artifact = {
            "id": "g_sales",
            "indicator": "Receita por regiao",
            "type": "bar",
            "component_preset_id": "bar_ranked",
            "render_mode": "library",
            "visual_options": {
                "orientation": "horizontal",
                "top_n": 5,
                "show_values": True,
                "aggregate_others": True,
            },
            "data_binding": {
                "source": "sales.csv",
                "roles": {"category": "region", "value": "revenue"},
                "aggregation": "sum",
                "filters": [],
            },
        }
        script = assemble_script(
            {},
            [{"id": "g_sales", "page": 1, "x": 10, "y": 20, "w": 120, "h": 80}],
            {"sales.csv": csv_path},
            output_pdf,
            settings={"title": "Library Test", "visual_theme_id": "executive_clean", "chart_style_id": "modern"},
            include_title=False,
            artifacts=[artifact],
        )

        self.assertIn("import pdf_report_renderers.runtime as render_runtime", script)
        self.assertIn("from pdf_report_renderers.runtime import", script)
        self.assertNotIn("def render_bar_chart", script)
        self.assertNotIn("def render_donut_chart", script)
        self.assertNotIn("def render_gauge_chart", script)

        result = execute_script(script, script_path, timeout=30)

        self.assertTrue(result["success"], result.get("stderr") or result.get("stdout"))
        self.assertTrue(os.path.exists(output_pdf))
        with open(f"{output_pdf}.manifest.json", "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        self.assertEqual(manifest["components"][0]["id"], "g_sales")
        self.assertEqual(manifest["components"][0]["status"], "rendered")

    def test_library_count_aggregation_accepts_categorical_values(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        aggregate = namespace["_library_aggregate"]

        result = aggregate(
            pd.DataFrame({
                "type_1": ["Water", "Water", "Grass", "Fire", "Fire", "Fire"],
                "name": ["A", "B", "C", "D", "E", "F"],
            }),
            ["type_1"],
            "name",
            "count",
        )

        counts = dict(zip(result["type_1"], result["name"]))
        self.assertEqual(counts, {"Fire": 3, "Grass": 1, "Water": 2})


if __name__ == "__main__":
    unittest.main()
