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
import json
import logging
from pathlib import Path

from app.utils.component_renderers import sandbox_runtime_source
from app.utils.report_paths import ReportPaths


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = ROOT_DIR / "workspace"
SCRIPT_DIR = WORKSPACE_DIR / "scripts"
FILTERED_DIR = WORKSPACE_DIR / "filtered"
REPORTS_DIR = WORKSPACE_DIR / "reports"
UPLOADS_DIR = WORKSPACE_DIR / "uploads"
SANDBOX_SUPPORT_DIR = WORKSPACE_DIR / "sandbox_support"
MAX_OUTPUT_FILE_BYTES = int(os.getenv("PDF_REPORT_SANDBOX_MAX_FILE_BYTES", str(200 * 1024 * 1024)))
logger = logging.getLogger(__name__)
SYSTEM_BIND_PATHS = [
    "/bin",
    "/etc",
    "/lib",
    "/lib64",
    "/sbin",
    "/usr",
    "/usr/local",
]


SANDBOX_GUARD = r'''
import builtins
import atexit
import ctypes
import io
import multiprocessing
import os
import pathlib
import json
import socket
import subprocess
import sys


def _split_paths(value):
    return [os.path.realpath(path) for path in value.split(os.pathsep) if path]


READ_DIRS = _split_paths(os.environ.get("PDF_REPORT_SANDBOX_READ_DIRS", ""))
WRITE_DIRS = _split_paths(os.environ.get("PDF_REPORT_SANDBOX_WRITE_DIRS", ""))
MANIFEST_PATH = os.environ.get("PDF_REPORT_SANDBOX_AUDIT_LOG_PATH", "")


_manifest_write_in_progress = False
_manifest_entries = []


def _manifest_path_value(path):
    if path is None:
        return None
    try:
        return os.path.realpath(os.fspath(path))
    except TypeError:
        return str(path)


def _append_manifest(event, path=None, mode=None, denied=False):
    if not MANIFEST_PATH:
        return
    _manifest_entries.append({
        "event": event,
        "path": _manifest_path_value(path),
        "mode": mode,
        "denied": bool(denied),
    })


def _flush_manifest():
    global _manifest_write_in_progress
    if not MANIFEST_PATH or not _manifest_entries:
        return
    try:
        _manifest_write_in_progress = True
        with _original_open(MANIFEST_PATH, "a", encoding="utf-8") as manifest:
            for entry in _manifest_entries:
                manifest.write(json.dumps(entry, ensure_ascii=False) + "\\n")
    except Exception:
        pass
    finally:
        _manifest_write_in_progress = False


atexit.register(_flush_manifest)


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
        _append_manifest("open", real_path, mode, denied=True)
        raise PermissionError(f"Sandbox denied filesystem access: {real_path}")


_original_open = builtins.open


def _guarded_open(file, mode="r", *args, **kwargs):
    _check_path(file, mode)
    _append_manifest("open", file, mode)
    return _original_open(file, mode, *args, **kwargs)


builtins.open = _guarded_open
io.open = _guarded_open

_original_path_open = pathlib.Path.open


def _guarded_path_open(self, mode="r", *args, **kwargs):
    _check_path(self, mode)
    _append_manifest("path.open", self, mode)
    return _original_path_open(self, mode, *args, **kwargs)


pathlib.Path.open = _guarded_path_open

_original_os_open = os.open


def _guarded_os_open(path, flags, mode=0o777, *args, **kwargs):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    mode_text = "w" if flags & write_flags else "r"
    _check_path(path, mode_text)
    _append_manifest("os.open", path, mode_text)
    return _original_os_open(path, flags, mode, *args, **kwargs)


os.open = _guarded_os_open

try:
    import posix as _posix
except ImportError:  # pragma: no cover - non-POSIX platforms
    _posix = None
if _posix is not None:
    # os.open is an alias of posix.open; patch the low-level module too.
    _posix.open = _guarded_os_open


def _deny_process_call(*args, **kwargs):
    _append_manifest("process", None, denied=True)
    raise PermissionError("Sandbox denied process execution")


_PROCESS_FUNCTIONS = (
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "fork", "forkpty", "posix_spawn", "posix_spawnp",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
)
for _module in (os, _posix):
    if _module is None:
        continue
    for _name in _PROCESS_FUNCTIONS:
        if hasattr(_module, _name):
            setattr(_module, _name, _deny_process_call)
    if hasattr(_module, "system"):
        setattr(_module, "system", _deny_process_call)

try:
    import _posixsubprocess
    _posixsubprocess.fork_exec = _deny_process_call
except ImportError:  # pragma: no cover - non-POSIX platforms
    pass


def _deny_os_system(*args, **kwargs):
    _append_manifest("os.system", None, denied=True)
    raise PermissionError("Sandbox denied process execution")


def _deny_os_popen(*args, **kwargs):
    _append_manifest("os.popen", None, denied=True)
    raise PermissionError("Sandbox denied process execution")


def _deny_subprocess(*args, **kwargs):
    _append_manifest("subprocess.Popen", None, denied=True)
    raise PermissionError("Sandbox denied process execution")


def _deny_multiprocessing(*args, **kwargs):
    _append_manifest("multiprocessing.Process.start", None, denied=True)
    raise PermissionError("Sandbox denied process execution")


os.system = _deny_os_system
os.popen = _deny_os_popen
subprocess.Popen = _deny_subprocess
subprocess.run = _deny_subprocess
subprocess.call = _deny_subprocess
subprocess.check_call = _deny_subprocess
subprocess.check_output = _deny_subprocess
multiprocessing.Process.start = _deny_multiprocessing


def _deny_ctypes(*args, **kwargs):
    _append_manifest("ctypes.dlopen", None, denied=True)
    raise PermissionError("Sandbox denied ctypes access")


ctypes.CDLL = _deny_ctypes
ctypes.PyDLL = _deny_ctypes
if hasattr(ctypes, "WinDLL"):
    ctypes.WinDLL = _deny_ctypes
if hasattr(ctypes, "OleDLL"):
    ctypes.OleDLL = _deny_ctypes
if hasattr(ctypes, "cdll"):
    ctypes.cdll.LoadLibrary = _deny_ctypes
if hasattr(ctypes, "pydll"):
    ctypes.pydll.LoadLibrary = _deny_ctypes


def _deny_network(*args, **kwargs):
    _append_manifest("network", None, denied=True)
    raise PermissionError("Sandbox denied network access")


socket.socket.connect = _deny_network
socket.socket.connect_ex = _deny_network
socket.create_connection = _deny_network


def _audit_hook(event, args):
    if event == "open":
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else None
        _append_manifest("open", path, mode)
    elif event == "os.system":
        _append_manifest("os.system", args[0] if args else None, denied=True)
    elif event == "subprocess.Popen":
        _append_manifest("subprocess.Popen", args[0] if args else None, denied=True)
    elif event == "socket.connect":
        target = args[1] if len(args) > 1 else None
        _append_manifest("socket.connect", target, denied=True)
    elif event == "ctypes.dlopen":
        _append_manifest("ctypes.dlopen", args[0] if args else None, denied=True)


if hasattr(sys, "addaudithook"):
    sys.addaudithook(_audit_hook)
'''


def _ensure_runtime_dirs() -> None:
    for path in (SCRIPT_DIR, FILTERED_DIR, REPORTS_DIR, UPLOADS_DIR, SANDBOX_SUPPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
    sitecustomize_path = SANDBOX_SUPPORT_DIR / "sitecustomize.py"
    if not sitecustomize_path.exists() or sitecustomize_path.read_text(encoding="utf-8") != SANDBOX_GUARD:
        sitecustomize_path.write_text(SANDBOX_GUARD, encoding="utf-8")

    renderers_dir = SANDBOX_SUPPORT_DIR / "pdf_report_renderers"
    renderers_dir.mkdir(parents=True, exist_ok=True)
    init_path = renderers_dir / "__init__.py"
    runtime_path = renderers_dir / "runtime.py"
    init_content = ""
    runtime_content = sandbox_runtime_source()
    if not init_path.exists() or init_path.read_text(encoding="utf-8") != init_content:
        init_path.write_text(init_content, encoding="utf-8")
    if not runtime_path.exists() or runtime_path.read_text(encoding="utf-8") != runtime_content:
        runtime_path.write_text(runtime_content, encoding="utf-8")
    loader_path = SANDBOX_SUPPORT_DIR / "pdf_report_data.py"
    loader_source = (ROOT_DIR / "app" / "utils" / "tabular.py").read_text(encoding="utf-8")
    if not loader_path.exists() or loader_path.read_text(encoding="utf-8") != loader_source:
        loader_path.write_text(loader_source, encoding="utf-8")


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
        "PDF_REPORT_SANDBOX_AUDIT_LOG_PATH": str(tmp_dir / "access_manifest.jsonl"),
        "MPLCONFIGDIR": str(tmp_dir / "matplotlib"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(SANDBOX_SUPPORT_DIR),
        "PYTHONNOUSERSITE": "1",
        "TMPDIR": str(tmp_dir),
    }
    return {key: value for key, value in keep.items() if value}


def _is_under_system_path(path: Path) -> bool:
    return any(path == Path(root) or path.is_relative_to(root) for root in SYSTEM_BIND_PATHS)


def _python_runtime_roots() -> list[Path]:
    """Directories the interpreter needs that are not already bound as system paths.

    Uses sys.prefix rather than the resolved executable: a venv's bin/python is
    usually a symlink into /usr, which would otherwise hide the venv itself.
    """
    candidates = [
        sys.prefix,
        sys.exec_prefix,
        sys.base_prefix,
        sys.base_exec_prefix,
        os.path.dirname(os.path.realpath(sys.executable)),
    ]
    roots: list[Path] = []
    for candidate in candidates:
        path = Path(candidate).resolve()
        if not path.exists() or _is_under_system_path(path):
            continue
        if any(path == root or path.is_relative_to(root) for root in roots):
            continue
        roots = [root for root in roots if not root.is_relative_to(path)]
        roots.append(path)
    return roots


def _sandbox_bind_args(tmp_dir: Path, paths: ReportPaths | None = None) -> list[str]:
    args: list[str] = [
        "--unshare-all",
        "--new-session",
        "--die-with-parent",
        "--clearenv",
        *(["--proc", "/proc"] if _bwrap_mount_proc else ["--dir", "/proc"]),
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]

    _ensure_runtime_dirs()
    paths = (paths or ReportPaths("", None)).ensure()
    # Only this report's folders (or the shared legacy folders for old sessions).
    read_only_workspace = [path.resolve() for path in paths.sandbox_read_only] + [SANDBOX_SUPPORT_DIR.resolve()]
    writable_workspace = [path.resolve() for path in paths.sandbox_writable]
    runtime_roots = _python_runtime_roots()

    target_paths = [
        ROOT_DIR.resolve(),
        *read_only_workspace,
        *writable_workspace,
        tmp_dir.resolve(),
        *runtime_roots,
    ]
    created_dirs: set[str] = set()
    for target in target_paths:
        current = Path("/")
        for part in target.parts[1:]:
            current = current / part
            current_path = str(current)
            if current_path not in created_dirs and not _is_under_system_path(current):
                args.extend(["--dir", current_path])
                created_dirs.add(current_path)

    for system_path in SYSTEM_BIND_PATHS:
        if Path(system_path).exists():
            args.extend(["--ro-bind", system_path, system_path])
    for path in runtime_roots:
        args.extend(["--ro-bind", str(path), str(path)])
    for path in read_only_workspace:
        args.extend(["--ro-bind", str(path), str(path)])
    for path in writable_workspace:
        args.extend(["--bind", str(path), str(path)])
    args.extend(["--bind", str(tmp_dir), str(tmp_dir)])
    return args


def _limit_process_resources():
    limits = (
        (resource.RLIMIT_CPU, 20),
        (resource.RLIMIT_AS, 2 * 1024 * 1024 * 1024),
        (resource.RLIMIT_FSIZE, MAX_OUTPUT_FILE_BYTES),
    )
    for limit, value in limits:
        try:
            resource.setrlimit(limit, (value, value))
        except Exception:
            pass


def _run_with_bwrap(command: list[str], env: dict, timeout: int, tmp_dir: Path, paths: ReportPaths | None = None) -> subprocess.CompletedProcess:
    bwrap_command = ["bwrap", *_sandbox_bind_args(tmp_dir, paths)]
    for key, value in env.items():
        bwrap_command.extend(["--setenv", key, value])
    # Same working directory as the python-guard backend, so relative paths behave identically.
    bwrap_command.extend(["--chdir", str(ROOT_DIR.resolve())])
    bwrap_command.extend(command)
    return subprocess.run(
        bwrap_command,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT_DIR),
        env={"PATH": os.environ.get("PATH", "")},
        preexec_fn=_limit_process_resources if os.name == "posix" else None,
    )


def _run_python_guard(command: list[str], env: dict, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT_DIR),
        env=env,
        preexec_fn=_limit_process_resources if os.name == "posix" else None,
    )


class SandboxUnavailableError(RuntimeError):
    """Raised when no acceptable sandbox backend can run generated code."""

    def as_error_dict(self) -> dict:
        return {
            "code": "sandbox_unavailable",
            "message": str(self),
            "stage": "sandbox",
            "retryable": False,
        }


_bwrap_probe_result: tuple[bool, str] | None = None
_bwrap_mount_proc = True  # False when the host refuses a new /proc (see probe_bubblewrap)


def _unsafe_fallback_allowed() -> bool:
    return os.getenv("PDF_REPORT_ALLOW_UNSAFE_SANDBOX", "").strip() == "1"


def probe_bubblewrap(force: bool = False) -> tuple[bool, str]:
    """Check once per process that bubblewrap can start this interpreter."""
    global _bwrap_probe_result
    if _bwrap_probe_result is not None and not force:
        return _bwrap_probe_result
    if shutil.which("bwrap") is None:
        _bwrap_probe_result = (False, "bwrap is not installed")
        return _bwrap_probe_result
    global _bwrap_mount_proc
    _bwrap_mount_proc = True
    _bwrap_probe_result = _probe_once()
    if not _bwrap_probe_result[0] and "mount proc" in _bwrap_probe_result[1].lower():
        # Some container hosts (Docker Desktop on macOS, for one) refuse a new
        # /proc while Docker masks parts of the container's /proc. Generated
        # code doesn't need /proc, so run without it; all other isolation stays.
        _bwrap_mount_proc = False
        _bwrap_probe_result = _probe_once()
        if _bwrap_probe_result[0]:
            logger.info("bubblewrap cannot mount /proc on this host; the sandbox runs without /proc.")
        else:
            _bwrap_mount_proc = True
    return _bwrap_probe_result


def _probe_once() -> tuple[bool, str]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf-report-sandbox-probe-"))
    try:
        result = _run_with_bwrap(
            [sys.executable, "-I", "-c", "print('sandbox-ok')"],
            {"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            30,
            tmp_dir,
        )
        if result.returncode == 0 and result.stdout.strip() == "sandbox-ok":
            return (True, "")
        detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
        return (False, detail[-500:])
    except Exception as exc:
        return (False, str(exc))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def select_sandbox_backend() -> str:
    available, reason = probe_bubblewrap()
    if available:
        return "bubblewrap"
    if _unsafe_fallback_allowed():
        logger.warning("bubblewrap unavailable (%s); using python-guard because PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1", reason)
        return "python-guard"
    raise SandboxUnavailableError(
        "Generated code cannot run: the bubblewrap sandbox is unavailable "
        f"({reason}). Install bubblewrap and allow unprivileged user namespaces, "
        "or set PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1 only in trusted environments."
    )


def sandbox_status() -> dict:
    available, reason = probe_bubblewrap()
    return {
        "bubblewrap_available": available,
        "bubblewrap_error": reason,
        "sandbox_has_proc": _bwrap_mount_proc,
        "unsafe_fallback_allowed": _unsafe_fallback_allowed(),
    }


class SandboxExecutor:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def execute(self, script_content: str, script_path: str, paths: ReportPaths | None = None) -> dict:
        _ensure_runtime_dirs()
        backend = select_sandbox_backend()
        paths = (paths or ReportPaths("", None)).ensure()
        start_time = time.perf_counter()
        run_id = uuid.uuid4().hex
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"pdf-report-sandbox-{run_id}-"))
        (tmp_dir / "matplotlib").mkdir(parents=True, exist_ok=True)

        requested_script_path = Path(script_path)
        if not requested_script_path.is_absolute():
            requested_script_path = ROOT_DIR / requested_script_path
        resolved_script_path = requested_script_path.resolve()
        if resolved_script_path.is_relative_to(paths.scripts.resolve()):
            user_script_path = resolved_script_path
        else:
            user_script_path = tmp_dir / f"sandbox_{run_id}.py"

        user_script_path.parent.mkdir(parents=True, exist_ok=True)
        user_script_path.write_text(script_content, encoding="utf-8")

        wrapper_script_path = tmp_dir / f"runner_{run_id}.py"
        manifest_path = tmp_dir / "access_manifest.jsonl"
        wrapper_script_path.write_text(
            textwrap.dedent(
                f'''
                import json
                import runpy
                import sitecustomize
                from pathlib import Path

                USER_SCRIPT_PATH = {str(user_script_path)!r}
                MANIFEST_PATH = Path({str(manifest_path)!r})

                try:
                    runpy.run_path(USER_SCRIPT_PATH, run_name="__main__")
                finally:
                    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
                    original_open = getattr(sitecustomize, "_original_open", open)
                    with original_open(MANIFEST_PATH, "w", encoding="utf-8") as manifest:
                        for entry in getattr(sitecustomize, "_manifest_entries", []):
                            manifest.write(json.dumps(entry, ensure_ascii=False) + "\\n")
                '''
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        read_dirs = _existing_paths([
            *paths.sandbox_read_only,
            *paths.sandbox_writable,
            tmp_dir,
            SANDBOX_SUPPORT_DIR,
        ]) + _python_read_dirs()
        write_dirs = _existing_paths([*paths.sandbox_writable, tmp_dir])

        env = _safe_env(tmp_dir)
        env["PDF_REPORT_SANDBOX_READ_DIRS"] = os.pathsep.join(read_dirs)
        env["PDF_REPORT_SANDBOX_WRITE_DIRS"] = os.pathsep.join(write_dirs)
        env["HOME"] = "/tmp"
        env["TMPDIR"] = str(tmp_dir)
        env["PATH"] = "/usr/bin:/bin"
        env["PDF_REPORT_SANDBOX_BACKEND"] = backend

        base_result = {
            "sandbox_backend": backend,
            "sandbox_access_manifest_path": str(manifest_path),
        }
        try:
            command = [sys.executable, str(wrapper_script_path)]
            if backend == "bubblewrap":
                result = _run_with_bwrap(command, env, self.timeout, tmp_dir, paths)
            else:
                result = _run_python_guard(command, env, self.timeout)
            return {
                **base_result,
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "duration": time.perf_counter() - start_time,
                "sandbox_script_path": str(wrapper_script_path),
                "sandbox_user_script_path": str(user_script_path),
                "sandbox_access_manifest": self._read_access_manifest(manifest_path),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                **base_result,
                "success": False,
                "stdout": exc.stdout or "",
                "stderr": f"Sandbox execution timed out after {self.timeout}s",
                "returncode": None,
                "duration": time.perf_counter() - start_time,
                "sandbox_access_manifest": self._read_access_manifest(manifest_path),
            }
        except Exception as exc:
            return {
                **base_result,
                "success": False,
                "stdout": "",
                "stderr": str(exc),
                "returncode": None,
                "duration": time.perf_counter() - start_time,
                "sandbox_access_manifest": self._read_access_manifest(manifest_path),
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _read_access_manifest(manifest_path: Path) -> list[dict]:
        if not manifest_path.exists():
            return []
        entries = []
        try:
            with manifest_path.open("r", encoding="utf-8") as manifest:
                for line in manifest:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return []
        return entries


def execute_script(script_content: str, script_path: str, timeout: int = 30, paths: ReportPaths | None = None) -> dict:
    """
    Writes and executes generated Python inside a sandbox.

    bubblewrap is required: generated scripts see the uploads (read-only) and
    the filtered/scripts/reports folders (writable), but not session files,
    secrets, the network, or a writable interpreter. The in-process
    python-guard is only used when PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1, because
    it can be bypassed. Raises SandboxUnavailableError otherwise.

    Pass the report's ReportPaths so the script sees only that report's
    folders; without it the shared legacy folders are mounted.
    """
    return SandboxExecutor(timeout=timeout).execute(
        textwrap.dedent(script_content),
        script_path,
        paths,
    )
