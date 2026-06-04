import subprocess
import unittest
from pathlib import Path


class FrontendSanitizationTests(unittest.TestCase):
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
