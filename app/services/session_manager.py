import json
import os
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager

from pydantic import ValidationError

from app.models.session import normalize_session

SESSION_DIR = "workspace/sessions"

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

def get_session_path(job_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{job_id}.json")


def get_session_lock_path(job_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{job_id}.lock")


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
    path = get_session_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return normalize_session(json.load(f), job_id)

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


def set_session_stage(job_id: str, step: str, progress: int = None, error: dict = None):
    updates = {"current_step": step}
    if progress is not None:
        updates["progress"] = max(0, min(100, int(progress)))
    if error is not None:
        updates["error"] = error
        if error:
            updates["status"] = error.get("message", "Erro")
    return update_session(job_id, updates)
