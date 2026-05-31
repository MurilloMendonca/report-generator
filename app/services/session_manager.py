import json
import os
from datetime import datetime, timezone

from pydantic import ValidationError

from app.models.session import normalize_session

SESSION_DIR = "workspace/sessions"

def get_session_path(job_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{job_id}.json")

def create_session(job_id: str, data: dict):
    os.makedirs(SESSION_DIR, exist_ok=True)
    data = normalize_session(data, job_id) or {}
    with open(get_session_path(job_id), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_session(job_id: str) -> dict:
    path = get_session_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return normalize_session(json.load(f), job_id)

def update_session(job_id: str, updates: dict):
    session = get_session(job_id)
    if session:
        updates.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        session.update(updates)
        create_session(job_id, session)
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
    session = get_session(job_id)
    if session:
        logs = session.get("logs", [])
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        logs.append(f"[{timestamp}] {message}")
        session["logs"] = logs
        session["status"] = message
        create_session(job_id, session)
