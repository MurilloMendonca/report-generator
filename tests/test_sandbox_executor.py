import os
import subprocess
import sys
import socket
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.services import executor_service
from app.services.executor_service import SandboxUnavailableError, execute_script, probe_bubblewrap

ROOT_DIR = Path(__file__).resolve().parents[1]
BWRAP_AVAILABLE, BWRAP_ERROR = probe_bubblewrap()


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
        self.assertTrue(result["sandbox_access_manifest"])
        self.assertTrue(any(entry.get("path") == os.path.abspath(output_path) for entry in result["sandbox_access_manifest"]))

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

    def test_allows_openpyxl_excel_reads(self):
        import pandas as pd

        input_path = f"workspace/uploads/test_sandbox_{uuid.uuid4().hex}.xlsx"
        self.created_paths.append(input_path)
        pd.DataFrame({"name": ["Pikachu"], "is_legendary": [False]}).to_excel(input_path, index=False)

        result = execute_script(
            "\n".join([
                "import pandas as pd",
                f"df = pd.read_excel({input_path!r})",
                "print(df.shape[0])",
            ]),
            self.script_path(),
        )

        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["stdout"].strip(), "1")

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

    def test_denies_subprocess_execution(self):
        result = execute_script(
            "import subprocess\nsubprocess.run(['cat', '.env'], check=False)",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied process execution", result["stderr"])
        self.assertTrue(any(entry.get("event") == "subprocess.Popen" for entry in result["sandbox_access_manifest"]))

    def test_denies_os_system_execution(self):
        result = execute_script(
            "import os\nos.system('cat .env')",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied process execution", result["stderr"])
        self.assertTrue(any(entry.get("event") == "os.system" for entry in result["sandbox_access_manifest"]))

    def test_denies_ctypes_import(self):
        result = execute_script(
            "import ctypes\nctypes.CDLL(None)",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied ctypes access", result["stderr"])
        self.assertTrue(any(entry.get("event") == "ctypes.dlopen" for entry in result["sandbox_access_manifest"]))

    def test_denies_low_level_posix_open_outside_workspace(self):
        result = execute_script(
            "import posix\nposix.open('.env', posix.O_RDONLY)",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied filesystem access", result["stderr"])

    def test_denies_low_level_process_spawning(self):
        result = execute_script(
            "import os\nos.execv('/bin/cat', ['cat', '.env'])",
            self.script_path(),
        )

        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied process execution", result["stderr"])

    def test_times_out_infinite_loops(self):
        result = execute_script("while True:\n    pass", self.script_path(), timeout=1)

        self.assertFalse(result["success"])
        self.assertIn("timed out", result["stderr"])



@unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
class BubblewrapIsolationTests(unittest.TestCase):
    """These scripts deliberately skip the Python-level guard (via _io.FileIO and
    os.listdir) to check that the bubblewrap mounts hold by themselves."""

    def run_script(self, code):
        script_path = f"workspace/scripts/test_bwrap_{uuid.uuid4().hex}.py"
        self.addCleanup(lambda: os.path.exists(script_path) and os.remove(script_path))
        return execute_script(code, script_path)

    def test_uses_bubblewrap_backend(self):
        result = self.run_script("print('ok')")
        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["sandbox_backend"], "bubblewrap")

    def test_network_namespace_blocks_host_database_even_without_python_guard(self):
        from app.utils.report_paths import ReportPaths
        import shutil
        paths = ReportPaths.for_new_report(f"test-network-{uuid.uuid4().hex}").ensure()
        self.addCleanup(shutil.rmtree, paths.root, True)
        with socket.socket() as listener, tempfile.TemporaryDirectory() as tmp:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            # -S disables sitecustomize and its Python network guard entirely.
            code = "import socket\nfor host, port in " + repr([("127.0.0.1", port), ("127.0.0.1", 5433), ("198.51.100.1", 5432)]) + ":\n s=socket.socket(); s.settimeout(0.2)\n try:\n  s.connect((host, port)); raise AssertionError('Network escaped')\n except OSError:\n  print('blocked')\n finally:\n  s.close()"
            result = executor_service._run_with_bwrap([sys.executable, "-S", "-c", code], {"PATH": os.defpath}, 5, Path(tmp), paths=paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ["blocked"] * 3)

    def test_interpreter_and_uploads_are_read_only(self):
        for target in [Path(sys.prefix) / f"probe_{uuid.uuid4().hex}", ROOT_DIR / "workspace" / "uploads" / f"probe_{uuid.uuid4().hex}"]:
            self.addCleanup(lambda t=target: t.exists() and t.unlink())
            result = self.run_script(f"import _io\n_io.FileIO({str(target)!r}, 'w').write(b'x')")
            self.assertFalse(result["success"], target)
            self.assertIn("Read-only file system", result["stderr"])
            self.assertFalse(target.exists())

    def test_sessions_and_source_are_not_visible(self):
        for directory in ["workspace/sessions", "app"]:
            result = self.run_script(f"import os\nprint(os.listdir({str(ROOT_DIR / directory)!r}))")
            self.assertFalse(result["success"], directory)
            self.assertIn("FileNotFoundError", result["stderr"])

    def test_report_outputs_stay_writable(self):
        target = ROOT_DIR / "workspace" / "reports" / f"probe_{uuid.uuid4().hex}"
        self.addCleanup(lambda: target.exists() and target.unlink())
        result = self.run_script(f"import _io\n_io.FileIO({str(target)!r}, 'w').write(b'x')")
        self.assertTrue(result["success"], result["stderr"])
        self.assertTrue(target.exists())


class SandboxBackendSelectionTests(unittest.TestCase):
    def test_ci_requires_working_bubblewrap(self):
        if os.getenv("PDF_REPORT_REQUIRE_BWRAP") != "1":
            self.skipTest("set PDF_REPORT_REQUIRE_BWRAP=1 to require bubblewrap")
        self.assertTrue(BWRAP_AVAILABLE, BWRAP_ERROR)

    def test_refuses_to_run_without_bubblewrap(self):
        with patch.object(executor_service, "probe_bubblewrap", return_value=(False, "missing")), \
             patch.dict(os.environ, {"PDF_REPORT_ALLOW_UNSAFE_SANDBOX": ""}):
            with self.assertRaises(SandboxUnavailableError) as error:
                execute_script("print('never')", "workspace/scripts/never.py")
        self.assertEqual(error.exception.as_error_dict()["code"], "sandbox_unavailable")
        self.assertFalse(os.path.exists("workspace/scripts/never.py"))

    def test_unsafe_fallback_requires_explicit_opt_in(self):
        script_path = f"workspace/scripts/test_fallback_{uuid.uuid4().hex}.py"
        self.addCleanup(lambda: os.path.exists(script_path) and os.remove(script_path))
        with patch.object(executor_service, "probe_bubblewrap", return_value=(False, "missing")), \
             patch.dict(os.environ, {"PDF_REPORT_ALLOW_UNSAFE_SANDBOX": "1"}):
            result = execute_script("print('guarded')", script_path)
        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["sandbox_backend"], "python-guard")

    def test_falls_back_to_no_proc_when_the_host_refuses_to_mount_it(self):
        """Docker Desktop on macOS refuses a new /proc while the container's /proc is masked."""
        calls = []

        def fake_run(command, env, timeout, tmp_dir, *args, **kwargs):
            args = executor_service._sandbox_bind_args(tmp_dir)
            calls.append("--proc" in args)
            if "--proc" in args:
                return subprocess.CompletedProcess(command, 1, "", "bwrap: Can't mount proc on /proc: Operation not permitted")
            return subprocess.CompletedProcess(command, 0, "sandbox-ok\n", "")

        self.addCleanup(executor_service.probe_bubblewrap, True)
        with patch.object(executor_service.shutil, "which", return_value="/usr/bin/bwrap"), \
             patch.object(executor_service, "_run_with_bwrap", side_effect=fake_run):
            self.assertEqual(executor_service.probe_bubblewrap(force=True), (True, ""))
            self.assertEqual(calls, [True, False])
            self.assertFalse(executor_service.sandbox_status()["sandbox_has_proc"])

    def test_other_failures_do_not_trigger_the_fallback(self):
        self.addCleanup(executor_service.probe_bubblewrap, True)
        failure = subprocess.CompletedProcess([], 1, "", "bwrap: No permissions to create a new namespace")
        with patch.object(executor_service.shutil, "which", return_value="/usr/bin/bwrap"), \
             patch.object(executor_service, "_run_with_bwrap", return_value=failure) as run:
            available, reason = executor_service.probe_bubblewrap(force=True)
        self.assertFalse(available)
        self.assertIn("namespace", reason)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(executor_service._bwrap_mount_proc)

    @unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
    def test_report_code_and_isolation_work_without_proc(self):
        script_path = ROOT_DIR / "workspace" / "scripts" / f"test_noproc_{uuid.uuid4().hex}.py"
        self.addCleanup(lambda: script_path.exists() and script_path.unlink())
        code = "\n".join([
            "import os, pandas as pd, matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "print(pd.DataFrame({'a': [1, 2, 3]}).a.sum(), os.path.exists('/proc/self'))",
            "plt.subplots()",
            f"print(os.listdir({str(ROOT_DIR / 'app')!r}))",
        ])
        with patch.object(executor_service, "_bwrap_mount_proc", False):
            result = execute_script(code, str(script_path))
        self.assertIn("6 False", result["stdout"])
        self.assertFalse(result["success"])
        self.assertIn("FileNotFoundError", result["stderr"])  # the source tree stays hidden

    def test_runtime_roots_use_venv_prefix_not_resolved_executable(self):
        roots = executor_service._python_runtime_roots()
        prefix = Path(sys.prefix).resolve()
        if not executor_service._is_under_system_path(prefix):
            self.assertTrue(any(prefix == root or prefix.is_relative_to(root) for root in roots))
        self.assertFalse(any(executor_service._is_under_system_path(root) for root in roots))


if __name__ == "__main__":
    unittest.main()
