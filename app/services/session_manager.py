import json
import os

SESSION_DIR = "workspace/sessions"

def get_session_path(job_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{job_id}.json")

def create_session(job_id: str, data: dict):
    os.makedirs(SESSION_DIR, exist_ok=True)
    with open(get_session_path(job_id), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_session(job_id: str) -> dict:
    path = get_session_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def update_session(job_id: str, updates: dict):
    session = get_session(job_id)
    if session:
        session.update(updates)
        create_session(job_id, session)
        return session
    return None

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
