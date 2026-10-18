import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed (e.g. inside the production image)")
class FrontendSanitizationTests(unittest.TestCase):
    def test_layout_history_and_page_bounds(self):
        script = Path(__file__).with_name("layout_state.spec.js")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_template_editor_loads_with_its_actual_page_dependencies(self):
        script = Path(__file__).with_name("template_editor.spec.js")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_upload_polling_and_unsaved_settings_workflows(self):
        script = Path(__file__).with_name("frontend_workflow.spec.js")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_xss_payloads_render_as_text(self):
        script = Path(__file__).with_name("frontend_sanitization.spec.js")
        result = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
