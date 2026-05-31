import os
import unittest
import uuid

from app.services.executor_service import execute_script


class SandboxExecutorTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []

    def tearDown(self):
        for path in self.created_paths:
            if path and os.path.exists(path):
                os.remove(path)

    def script_path(self):
        path = f"workspace/scripts/test_sandbox_{uuid.uuid4().hex}.py"
        self.created_paths.append(path)
        return path

    def test_allows_expected_workspace_outputs(self):
        output_path = f"workspace/reports/test_sandbox_{uuid.uuid4().hex}.txt"
        self.created_paths.append(output_path)

        result = execute_script(
            f"open({output_path!r}, 'w', encoding='utf-8').write('ok')\nprint('done')",
            self.script_path(),
        )

        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["stdout"].strip(), "done")
        self.assertTrue(os.path.exists(output_path))

    def test_allows_matplotlib_report_assets(self):
        output_path = f"workspace/reports/test_sandbox_{uuid.uuid4().hex}.png"
        self.created_paths.append(output_path)

        result = execute_script(
            "\n".join([
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "fig, ax = plt.subplots()",
                "ax.plot([1, 2, 3], [2, 4, 6])",
                f"fig.savefig({output_path!r})",
                "print('chart-ok')",
            ]),
            self.script_path(),
        )

        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["stdout"].strip(), "chart-ok")
        self.assertTrue(os.path.exists(output_path))

    def test_allows_pdf_report_outputs(self):
        output_path = f"workspace/reports/test_sandbox_{uuid.uuid4().hex}.pdf"
        self.created_paths.append(output_path)

        result = execute_script(
            "\n".join([
                "from fpdf import FPDF",
                "pdf = FPDF()",
                "pdf.add_page()",
                "pdf.set_font('Helvetica', size=12)",
                "pdf.cell(40, 10, 'ok')",
                f"pdf.output({output_path!r})",
                "print('pdf-ok')",
            ]),
            self.script_path(),
        )

        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["stdout"].strip(), "pdf-ok")
        self.assertTrue(os.path.exists(output_path))

    def test_denies_project_secret_reads(self):
        result = execute_script(
            "open('.env', 'r', encoding='utf-8').read()",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied filesystem access", result["stderr"])

    def test_denies_writes_outside_allowed_workspace(self):
        blocked_path = f"/tmp/pdf_report_sandbox_blocked_{uuid.uuid4().hex}.txt"
        self.created_paths.append(blocked_path)

        result = execute_script(
            f"open({blocked_path!r}, 'w', encoding='utf-8').write('blocked')",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied filesystem access", result["stderr"])
        self.assertFalse(os.path.exists(blocked_path))

    def test_denies_network_access(self):
        result = execute_script(
            "import socket\nsocket.create_connection(('example.com', 80), timeout=1)",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied network access", result["stderr"])

    def test_times_out_infinite_loops(self):
        result = execute_script("while True:\n    pass", self.script_path(), timeout=1)

        self.assertFalse(result["success"])
        self.assertIn("timed out", result["stderr"])


if __name__ == "__main__":
    unittest.main()
