import os
import shutil
from datetime import datetime, timezone, timedelta
from typing import TypedDict

from app.services.session_manager import get_session, get_session_path, get_session_lock_path

class RetentionPolicy(TypedDict):
    sessions_days: int
    reports_days: int
    scripts_days: int
    uploads_days: int
    filtered_days: int

DEFAULT_RETENTION: RetentionPolicy = {
    "sessions_days": 30,
    "reports_days": 30,
    "scripts_days": 7,
    "uploads_days": 30,
    "filtered_days": 7
}

WORKSPACE_DIRS = {
    "sessions": "workspace/sessions",
    "uploads": "workspace/uploads",
    "filtered": "workspace/filtered",
    "scripts": "workspace/scripts",
    "reports": "workspace/reports",
}

def _workspace_root() -> str:
    return os.path.abspath("workspace")


def _is_workspace_path(path: str) -> bool:
    try:
        return os.path.commonpath([_workspace_root(), os.path.abspath(path)]) == _workspace_root()
    except ValueError:
        return False


def safe_remove_path(path: str):
    """Removes a file or directory safely within the workspace."""
    if not path or not os.path.exists(path):
        return False

    target = os.path.abspath(path)
    if not _is_workspace_path(target):
        return False

    try:
        if os.path.isfile(target) or os.path.islink(target):
            os.remove(target)
            return True
        if os.path.isdir(target):
            shutil.rmtree(target)
            return True
    except OSError:
        return False
    return False


def _session_prefixes(job_id: str) -> tuple[str, ...]:
    return (
        job_id,
        f"{job_id}_",
        f"{job_id}-",
        f"preprocess_{job_id}",
        f"explore_{job_id}",
    )


def _scan_workspace_paths(job_id: str) -> set[str]:
    paths: set[str] = set()
    for directory in WORKSPACE_DIRS.values():
        if not os.path.isdir(directory):
            continue
        for filename in os.listdir(directory):
            if filename.startswith(_session_prefixes(job_id)):
                paths.add(os.path.join(directory, filename))
    return paths

def get_session_generated_paths(job_id: str, session: dict) -> set[str]:
    """Lists all paths associated with a specific session."""
    paths = {
        session.get("script_path"),
        session.get("pdf_path"),
        f"{session.get('pdf_path')}.manifest.json" if session.get("pdf_path") else None,
        get_session_path(job_id),
        get_session_lock_path(job_id),
    }

    # Files referenced in session
    for file_info in session.get("files", []):
        paths.update({
            file_info.get("path"),
            file_info.get("preprocessed_path"),
            file_info.get("filtered_path"),
        })

    paths.update(_scan_workspace_paths(job_id))
    return {p for p in paths if p and isinstance(p, str)}

def delete_entire_session(job_id: str) -> int:
    """Deletes a session and all its associated artifacts."""
    session = get_session(job_id)
    if not session:
        # Try to delete session files even if JSON is corrupted or missing.
        paths = {
            get_session_path(job_id),
            get_session_lock_path(job_id),
            *_scan_workspace_paths(job_id),
        }
        removed = 0
        for p in paths:
            if safe_remove_path(p):
                removed += 1
        return removed

    paths = get_session_generated_paths(job_id, session)
    removed = 0
    for p in sorted(list(paths)):
        if safe_remove_path(p):
            removed += 1
    return removed

def cleanup_expired_artifacts(policy: RetentionPolicy = None) -> dict:
    """Scans workspace and removes expired files and sessions."""
    policy = policy or DEFAULT_RETENTION
    now = datetime.now(timezone.utc)
    stats = {"sessions_removed": 0, "files_removed": 0, "errors": 0}
    protected_paths: set[str] = set()

    session_dir = WORKSPACE_DIRS["sessions"]
    if os.path.exists(session_dir):
        for filename in os.listdir(session_dir):
            if not filename.endswith(".json"):
                continue

            job_id = filename[:-5]
            session = get_session(job_id)
            if not session:
                continue
            protected_paths.update(get_session_generated_paths(job_id, session))

    # 1. Clean up sessions
    if os.path.exists(session_dir):
        for filename in os.listdir(session_dir):
            if not filename.endswith(".json"):
                continue

            job_id = filename[:-5]
            path = os.path.join(session_dir, filename)
            mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)

            if now - mtime > timedelta(days=policy["sessions_days"]):
                try:
                    stats["files_removed"] += delete_entire_session(job_id)
                    stats["sessions_removed"] += 1
                except Exception:
                    stats["errors"] += 1

    # 2. Clean up orphan files in other directories based on age
    for category, directory in WORKSPACE_DIRS.items():
        if category == "sessions":
            continue

        days = policy.get(f"{category}_days", 30)
        if not os.path.exists(directory):
            continue

        for filename in os.listdir(directory):
            path = os.path.join(directory, filename)
            if path in protected_paths:
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)

            if now - mtime > timedelta(days=days):
                if safe_remove_path(path):
                    stats["files_removed"] += 1

    return stats

def get_workspace_usage() -> dict:
    """Calculates disk usage for each workspace directory."""
    usage = {}
    total_bytes = 0
    
    for category, directory in WORKSPACE_DIRS.items():
        dir_size = 0
        if os.path.exists(directory):
            for root, _, files in os.walk(directory):
                for f in files:
                    fp = os.path.join(root, f)
                    if not os.path.islink(fp):
                        dir_size += os.path.getsize(fp)
        
        usage[category] = {
            "size_bytes": dir_size,
            "human_size": f"{dir_size / (1024*1024):.2f} MB"
        }
        total_bytes += dir_size
        
    usage["total"] = {
        "size_bytes": total_bytes,
        "human_size": f"{total_bytes / (1024*1024):.2f} MB"
    }
    return usage
