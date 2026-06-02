import os
import resource
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import textwrap
import time
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = ROOT_DIR / "workspace"
SCRIPT_DIR = WORKSPACE_DIR / "scripts"
FILTERED_DIR = WORKSPACE_DIR / "filtered"
REPORTS_DIR = WORKSPACE_DIR / "reports"
UPLOADS_DIR = WORKSPACE_DIR / "uploads"
SANDBOX_SUPPORT_DIR = WORKSPACE_DIR / "sandbox_support"


SANDBOX_GUARD = r'''
import builtins
import io
import os
import pathlib
import socket
import subprocess


def _split_paths(value):
    return [os.path.realpath(path) for path in value.split(os.pathsep) if path]


READ_DIRS = _split_paths(os.environ.get("PDF_REPORT_SANDBOX_READ_DIRS", ""))
WRITE_DIRS = _split_paths(os.environ.get("PDF_REPORT_SANDBOX_WRITE_DIRS", ""))


def _is_within(path, roots):
    real_path = os.path.realpath(os.fspath(path))
    for root in roots:
        try:
            if os.path.commonpath([real_path, root]) == root:
                return True
        except ValueError:
            continue
    return False


def _is_write_mode(mode):
    mode_text = str(mode)
    return any(token in mode_text for token in ("w", "a", "x", "+"))


def _check_path(path, mode="r"):
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    real_path = os.path.realpath(os.fspath(path))
    allowed_roots = WRITE_DIRS if _is_write_mode(mode) else READ_DIRS + WRITE_DIRS
    if not _is_within(real_path, allowed_roots):
        raise PermissionError(f"Sandbox denied filesystem access: {real_path}")


_original_open = builtins.open


def _guarded_open(file, mode="r", *args, **kwargs):
    _check_path(file, mode)
    return _original_open(file, mode, *args, **kwargs)


builtins.open = _guarded_open
io.open = _guarded_open

_original_path_open = pathlib.Path.open


def _guarded_path_open(self, mode="r", *args, **kwargs):
    _check_path(self, mode)
    return _original_path_open(self, mode, *args, **kwargs)


pathlib.Path.open = _guarded_path_open

_original_os_open = os.open


def _guarded_os_open(path, flags, mode=0o777, *args, **kwargs):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    _check_path(path, "w" if flags & write_flags else "r")
    return _original_os_open(path, flags, mode, *args, **kwargs)


os.open = _guarded_os_open


def _deny_process(*args, **kwargs):
    raise PermissionError("Sandbox denied process execution")


os.system = _deny_process
os.popen = _deny_process
subprocess.Popen = _deny_process
subprocess.run = _deny_process
subprocess.call = _deny_process
subprocess.check_call = _deny_process
subprocess.check_output = _deny_process


def _deny_network(*args, **kwargs):
    raise PermissionError("Sandbox denied network access")


socket.socket.connect = _deny_network
socket.socket.connect_ex = _deny_network
socket.create_connection = _deny_network
'''


def _ensure_runtime_dirs() -> None:
    for path in (SCRIPT_DIR, FILTERED_DIR, REPORTS_DIR, UPLOADS_DIR, SANDBOX_SUPPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
    sitecustomize_path = SANDBOX_SUPPORT_DIR / "sitecustomize.py"
    if not sitecustomize_path.exists() or sitecustomize_path.read_text(encoding="utf-8") != SANDBOX_GUARD:
        sitecustomize_path.write_text(SANDBOX_GUARD, encoding="utf-8")


def _existing_paths(paths):
    return [str(Path(path).resolve()) for path in paths if path and Path(path).exists()]


def _python_read_dirs():
    paths = {
        sys.prefix,
        sys.base_prefix,
        sysconfig.get_path("stdlib"),
        sysconfig.get_path("platstdlib"),
        sysconfig.get_path("purelib"),
        sysconfig.get_path("platlib"),
        "/etc/mime.types",
        "/etc/fonts",
        "/usr/share/fonts",
        "/usr/local/share/fonts",
    }
    return _existing_paths(paths)


def _safe_env(tmp_dir: Path) -> dict:
    keep = {
        "HOME": str(tmp_dir),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", ""),
        "MPLCONFIGDIR": str(tmp_dir / "matplotlib"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join([str(SANDBOX_SUPPORT_DIR), str(ROOT_DIR)]),
        "PYTHONNOUSERSITE": "1",
        "TMPDIR": str(tmp_dir),
    }
    return {key: value for key, value in keep.items() if value}


def _limit_process_resources():
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    except Exception:
        pass
    try:
        memory_limit = 2 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    except Exception:
        pass


class SandboxExecutor:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def execute(self, script_content: str, script_path: str) -> dict:
        _ensure_runtime_dirs()
        start_time = time.perf_counter()
        run_id = uuid.uuid4().hex
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"pdf-report-sandbox-{run_id}-"))
        (tmp_dir / "matplotlib").mkdir(parents=True, exist_ok=True)

        requested_script_path = Path(script_path)
        if not requested_script_path.is_absolute():
            requested_script_path = ROOT_DIR / requested_script_path
        try:
            relative_to_workspace = requested_script_path.resolve().is_relative_to(WORKSPACE_DIR.resolve())
        except ValueError:
            relative_to_workspace = False

        if relative_to_workspace:
            execution_script_path = requested_script_path
        else:
            execution_script_path = tmp_dir / f"sandbox_{run_id}.py"

        execution_script_path.parent.mkdir(parents=True, exist_ok=True)
        execution_script_path.write_text(script_content, encoding="utf-8")

        read_dirs = _existing_paths([
            UPLOADS_DIR,
            FILTERED_DIR,
            SCRIPT_DIR,
            REPORTS_DIR,
            tmp_dir,
        ]) + _python_read_dirs()
        write_dirs = _existing_paths([
            FILTERED_DIR,
            SCRIPT_DIR,
            REPORTS_DIR,
            tmp_dir,
        ])

        env = _safe_env(tmp_dir)
        env["PDF_REPORT_SANDBOX_READ_DIRS"] = os.pathsep.join(read_dirs)
        env["PDF_REPORT_SANDBOX_WRITE_DIRS"] = os.pathsep.join(write_dirs)

        try:
            result = subprocess.run(
                [sys.executable, str(execution_script_path)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(ROOT_DIR),
                env=env,
                preexec_fn=_limit_process_resources if os.name == "posix" else None,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "duration": time.perf_counter() - start_time,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "success": False,
                "stdout": exc.stdout or "",
                "stderr": f"Sandbox execution timed out after {self.timeout}s",
                "returncode": None,
                "duration": time.perf_counter() - start_time,
            }
        except Exception as exc:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(exc),
                "returncode": None,
                "duration": time.perf_counter() - start_time,
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def execute_script(script_content: str, script_path: str, timeout: int = 30) -> dict:
    """
    Writes and executes generated Python with a local sandbox.

    The sandbox is intentionally conservative for this app: generated scripts can
    read uploaded/filtered data and write scripts, filtered data and reports, but
    cannot read project secrets, write arbitrary filesystem paths, or open network
    sockets. Production deployments should still prefer container isolation.
    """
    return SandboxExecutor(timeout=timeout).execute(
        textwrap.dedent(script_content),
        script_path,
    )
