import json
import os
import logging
import re
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps

from pydantic import ValidationError

from app.models.session import normalize_session

SESSION_DIR = "workspace/sessions"
logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TERMINAL_STEPS = {"", "completed", "failed", "awaiting_clarification", "translation_failed"}


def is_valid_job_id(job_id) -> bool:
    return isinstance(job_id, str) and JOB_ID_PATTERN.fullmatch(job_id) is not None


def validate_job_id(job_id) -> str:
    """Reject ids that could escape the session folder or match other sessions' files."""
    if not is_valid_job_id(job_id):
        raise ValueError(f"Invalid session id: {job_id!r}")
    return job_id


def get_session_path(job_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{validate_job_id(job_id)}.json")


def get_session_lock_path(job_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{validate_job_id(job_id)}.lock")


@contextmanager
def session_lock(job_id: str):
    os.makedirs(SESSION_DIR, exist_ok=True)
    lock_path = get_session_lock_path(job_id)
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        if fcntl:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_session_file(job_id: str, data: dict):
    os.makedirs(SESSION_DIR, exist_ok=True)
    path = get_session_path(job_id)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{job_id}.", suffix=".tmp", dir=SESSION_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def create_session(job_id: str, data: dict):
    data = normalize_session(data, job_id) or {}
    with session_lock(job_id):
        write_session_file(job_id, data)

def get_session(job_id: str) -> dict:
    if not is_valid_job_id(job_id):
        return None
    path = get_session_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return normalize_session(data, job_id)
    except (json.JSONDecodeError, ValidationError, OSError):
        return None

def update_session(job_id: str, updates: dict):
    with session_lock(job_id):
        path = get_session_path(job_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            session = normalize_session(json.load(f), job_id)
        if session:
            updates.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
            session.update(updates)
            session = normalize_session(session, job_id)
            write_session_file(job_id, session)
            return session
    return None


def preserve_report_on_failure(refine):
    """Keep the last usable report when a refinement fails before a new PDF exists."""
    @wraps(refine)
    def guarded(job_id: str, *args, **kwargs):
        original = get_session(job_id) or {}
        fields = (
            "files", "data_filter", "data_preprocessing", "semantic_model",
            "document_insights", "artifacts", "artifact_validation", "template",
            "components", "calculated_metrics", "component_validation_retries",
            "settings", "script", "script_path", "pdf_path", "retry_count",
            "current_goal", "refinement_history", "pending_refinement",
        )
        snapshot = {key: deepcopy(original[key]) for key in fields if key in original}
        raised = False
        try:
            return refine(job_id, *args, **kwargs)
        except Exception:
            raised = True
            raise
        finally:
            latest = get_session(job_id) or {}
            if (
                original.get("pdf_version", 0) > 0
                and original.get("artifacts") and original.get("template")
                and (raised or latest.get("current_step") == "failed")
                and latest.get("pdf_version") == original.get("pdf_version")
            ):
                update_session(job_id, snapshot)
                add_log(job_id, "Refinamento falhou; o estado editável da última versão do relatório foi preservado.")
    return guarded


def append_exploration_turn(job_id: str, user_message: dict, assistant_message: dict):
    """Append a complete explorer exchange without losing concurrent session updates."""
    with session_lock(job_id):
        path = get_session_path(job_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as session_file:
            session = normalize_session(json.load(session_file), job_id)
        session["explore_history"] = [*session.get("explore_history", []), user_message, assistant_message]
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_session_file(job_id, normalize_session(session, job_id))
        return session

def list_recent_sessions(limit: int = 12) -> list:
    if not os.path.isdir(SESSION_DIR):
        return []

    sessions = []
    for filename in os.listdir(SESSION_DIR):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(SESSION_DIR, filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                session = normalize_session(json.load(f), filename[:-5])
        except (OSError, json.JSONDecodeError, ValidationError):
            continue

        stat = os.stat(path)
        sessions.append({
            "job_id": session.get("job_id") or filename[:-5],
            "initial_prompt": session.get("initial_prompt", ""),
            "status": session.get("status", ""),
            "file_count": len(session.get("files", [])),
            "pdf_version": session.get("pdf_version", 0),
            "created_at": session.get("created_at") or datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
            "updated_at": session.get("updated_at") or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })

    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions[:limit]

def add_log(job_id: str, message: str):
    with session_lock(job_id):
        path = get_session_path(job_id)
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            session = normalize_session(json.load(f), job_id)
        if session:
            logs = session.get("logs", [])
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
            logs.append(f"[{timestamp}] {message}")
            session["logs"] = logs
            session["status"] = message
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            write_session_file(job_id, normalize_session(session, job_id))


def append_stage_event(
    job_id: str,
    stage: str,
    event: str,
    *,
    status: str | None = None,
    duration_ms: float | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    details: dict | None = None,
):
    with session_lock(job_id):
        path = get_session_path(job_id)
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            session = normalize_session(json.load(f), job_id)
        if not session:
            return

        history = session.get("stage_history", [])
        history.append({
            "stage": stage,
            "event": event,
            "status": status or session.get("status", ""),
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            "provider": provider,
            "model_name": model_name,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        session["stage_history"] = history
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_session_file(job_id, normalize_session(session, job_id))
        logger.info(
            "stage_event job_id=%s stage=%s event=%s status=%s duration_ms=%s provider=%s model=%s",
            job_id,
            stage,
            event,
            status or session.get("status", ""),
            round(duration_ms, 1) if duration_ms is not None else "",
            provider or "",
            model_name or "",
        )


def record_llm_usage(
    job_id: str,
    operation: str,
    provider: str,
    model_name: str,
    *,
    attempt: int,
    duration_ms: float,
    status: str,
    prompt_chars: int = 0,
    response_chars: int = 0,
    error: str | None = None,
    cache_hit: bool = False,
):
    with session_lock(job_id):
        path = get_session_path(job_id)
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            session = normalize_session(json.load(f), job_id)
        if not session:
            return

        usage = session.get("llm_usage", {})
        usage.setdefault("calls", 0)
        usage.setdefault("attempts", 0)
        usage.setdefault("duration_ms", 0.0)
        usage.setdefault("by_provider", {})
        usage.setdefault("by_operation", {})
        usage.setdefault("history", [])
        usage.setdefault("cache_hits", 0)
        usage["calls"] += 1
        usage["attempts"] += int(attempt)
        usage["duration_ms"] = round(float(usage["duration_ms"]) + float(duration_ms), 1)
        usage["by_provider"][provider] = usage["by_provider"].get(provider, 0) + 1
        usage["by_operation"][operation] = usage["by_operation"].get(operation, 0) + 1
        if cache_hit:
            usage["cache_hits"] += 1
        usage["last_provider"] = provider
        usage["last_model_name"] = model_name
        usage["last_operation"] = operation
        usage["last_status"] = status
        usage["last_duration_ms"] = round(duration_ms, 1)
        usage["last_error"] = error or ""
        history = usage["history"]
        history.append({
            "operation": operation,
            "provider": provider,
            "model_name": model_name,
            "attempt": attempt,
            "duration_ms": round(duration_ms, 1),
            "status": status,
            "prompt_chars": int(prompt_chars),
            "response_chars": int(response_chars),
            "cache_hit": bool(cache_hit),
            "error": error or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        usage["history"] = history[-50:]
        session["llm_usage"] = usage
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_session_file(job_id, normalize_session(session, job_id))
        logger.info(
            "llm_usage job_id=%s operation=%s provider=%s model=%s attempt=%s duration_ms=%s status=%s cache_hit=%s",
            job_id,
            operation,
            provider,
            model_name,
            attempt,
            round(duration_ms, 1),
            status,
            cache_hit,
        )


def reserve_session_run(job_id: str, source: str = "pipeline") -> dict:
    with session_lock(job_id):
        path = get_session_path(job_id)
        if not os.path.exists(path):
            return {"run_id": 0, "previous_run_id": 0}
        with open(path, 'r', encoding='utf-8') as f:
            session = normalize_session(json.load(f), job_id)
        if not session:
            return {"run_id": 0, "previous_run_id": 0}

        previous_run_id = int(session.get("active_run_id", 0) or 0)
        next_run_id = int(session.get("run_sequence", 0) or 0) + 1
        history = session.get("run_history", [])
        history.append({
            "run_id": next_run_id,
            "source": source,
            "previous_run_id": previous_run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "active",
        })
        session["run_sequence"] = next_run_id
        session["active_run_id"] = next_run_id
        session["active_run_source"] = source
        session["run_history"] = history[-50:]
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_session_file(job_id, normalize_session(session, job_id))
        logger.info(
            "run_reserved job_id=%s run_id=%s previous_run_id=%s source=%s",
            job_id,
            next_run_id,
            previous_run_id,
            source,
        )
        return {"run_id": next_run_id, "previous_run_id": previous_run_id}


def is_current_session_run(job_id: str, run_id: int | None) -> bool:
    if not run_id:
        return False
    session = get_session(job_id)
    if not session:
        return False
    return int(session.get("active_run_id", 0) or 0) == int(run_id)


def set_session_stage(job_id: str, step: str, progress: int = None, error: dict = None):
    updates = {"current_step": step}
    if progress is not None:
        updates["progress"] = max(0, min(100, int(progress)))
    if error is not None:
        updates["error"] = error
        if error:
            updates["status"] = error.get("message", "Erro")
    return update_session(job_id, updates)


def recover_interrupted_sessions() -> list[str]:
    """Mark sessions left mid-run by a server restart as failed, so the UI stops waiting."""
    if not os.path.isdir(SESSION_DIR):
        return []
    recovered = []
    message = "O processamento foi interrompido porque o servidor reiniciou. Tente novamente."
    for filename in os.listdir(SESSION_DIR):
        job_id = filename[:-5]
        if not filename.endswith(".json") or not is_valid_job_id(job_id):
            continue
        session = get_session(job_id)
        if not session or session.get("current_step", "") in TERMINAL_STEPS:
            continue
        update_session(job_id, {
            "current_step": "failed",
            "progress": 100,
            "status": message,
            "active_run_id": 0,
            "error": {"code": "interrupted", "message": message, "stage": session.get("current_step", "")},
        })
        add_log(job_id, message)
        recovered.append(job_id)
    if recovered:
        logger.warning("Recovered %d interrupted session(s): %s", len(recovered), ", ".join(recovered))
    return recovered
