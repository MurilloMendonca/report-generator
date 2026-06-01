import json
import os
import unittest
import uuid

from app.services.executor_service import execute_script
from app.services.llm_service import assemble_script


class ReportScriptManifestTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []

    def tearDown(self):
        for path in self.created_paths:
            if os.path.exists(path):
                os.remove(path)

    def output_path(self):
        path = f"workspace/reports/test_manifest_{uuid.uuid4().hex}.pdf"
        self.created_paths.extend([path, f"{path}.manifest.json"])
        return os.path.abspath(path)

    def script_path(self):
        path = f"workspace/scripts/test_manifest_{uuid.uuid4().hex}.py"
        self.created_paths.append(path)
        return path

    def test_component_failure_fails_report_execution_and_writes_manifest(self):
        output_pdf = self.output_path()
        script = assemble_script(
            {
                "bad_component": (
                    "def render_bad_component(pdf, dataframes, x, y, w, h):\n"
                    "    raise ValueError('broken chart')"
                )
            },
            [{"id": "bad_component", "page": 1, "x": 10, "y": 30, "w": 100, "h": 40}],
            {},
            output_pdf,
            {"title": "Manifest Test", "font": "Arial", "font_size": 10, "alignment": "J", "line_height": 5},
        )

        result = execute_script(script, self.script_path())

        self.assertFalse(result["success"])
        self.assertIn("Report component rendering failed", result["stderr"])
        with open(f"{output_pdf}.manifest.json", "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["components"][0]["status"], "failed")
        self.assertEqual(manifest["components"][0]["id"], "bad_component")


if __name__ == "__main__":
    unittest.main()
