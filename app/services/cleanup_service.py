import os
import shutil
import stat
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import TypedDict

from sqlalchemy import func, select

from app import db
from app.services.session_manager import delete_session, get_session, is_valid_job_id, list_session_activity
from app.utils.report_paths import TENANTS_DIR, ReportPaths

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

# Legacy shared folders. Session documents live in the `reports` table.
WORKSPACE_DIRS = {
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

    directory_fd = None
    try:
        parts = Path(target).relative_to(Path(_workspace_root())).parts
        if not parts:
            return False
        directory_fd = os.open(_workspace_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for segment in parts[:-1]:
            child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        mode = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            shutil.rmtree(parts[-1], dir_fd=directory_fd)
        else:
            os.unlink(parts[-1], dir_fd=directory_fd)
        return True
    except OSError:
        return False
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return False


def _session_prefixes(job_id: str) -> tuple[str, ...]:
    return (
        f"{job_id}.",
        f"{job_id}_",
        f"{job_id}-",
        f"preprocess_{job_id}.",
        f"preprocess_{job_id}_",
        f"explore_{job_id}.",
        f"explore_{job_id}_",
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
    }

    # Files referenced in session
    for file_info in session.get("files", []):
        paths.update({
            file_info.get("path"),
            file_info.get("preprocessed_path"),
            file_info.get("filtered_path"),
        })

    paths.update(_scan_workspace_paths(job_id))
    try:
        report_paths = ReportPaths.from_session({**session, "job_id": job_id})
    except ValueError:
        report_paths = None
    if report_paths is not None and not report_paths.is_legacy:
        paths.add(str(report_paths.root))
    return {p for p in paths if p and isinstance(p, str)}

def delete_entire_session(job_id: str) -> int:
    """Deletes a session's row and all its files. Returns how many items were removed."""
    if not is_valid_job_id(job_id):
        return 0
    session = get_session(job_id)
    # Without a readable session, still remove files that carry its id.
    paths = get_session_generated_paths(job_id, session) if session else _scan_workspace_paths(job_id)
    removed = sum(1 for path in sorted(paths) if safe_remove_path(path))
    if delete_session(job_id):
        removed += 1
    return removed

def cleanup_expired_artifacts(policy: RetentionPolicy = None) -> dict:
    """Scans workspace and removes expired files and sessions."""
    policy = policy or DEFAULT_RETENTION
    now = datetime.now(timezone.utc)
    stats = {"sessions_removed": 0, "files_removed": 0, "errors": 0}
    protected_paths: set[str] = set()

    activity = list_session_activity()
    for job_id, _updated_at in activity:
        session = get_session(job_id)
        if session:
            protected_paths.update(get_session_generated_paths(job_id, session))

    # 1. Sessions not updated within the retention period
    for job_id, updated_at in activity:
        if now - updated_at > timedelta(days=policy["sessions_days"]):
            try:
                stats["files_removed"] += delete_entire_session(job_id)
                stats["sessions_removed"] += 1
            except Exception:
                stats["errors"] += 1

    # 2. Report folders whose session is gone (per-report layout)
    if TENANTS_DIR.is_dir():
        for report_dir in TENANTS_DIR.glob("*/reports/*"):
            path = str(report_dir)
            if path in protected_paths or not report_dir.is_dir():
                continue
            mtime = datetime.fromtimestamp(report_dir.stat().st_mtime, timezone.utc)
            if now - mtime > timedelta(days=policy["sessions_days"]) and safe_remove_path(path):
                stats["files_removed"] += 1

    # 3. Clean up orphan files in the legacy shared directories based on age
    for category, directory in WORKSPACE_DIRS.items():
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

    from app.services.auth_service import purge_expired
    stats["auth"] = purge_expired()
    return stats

def get_workspace_usage() -> dict:
    """Calculates disk usage for each workspace directory."""
    with db.transaction() as tx:
        session_bytes = int(tx.execute(select(func.pg_total_relation_size("reports"))).scalar() or 0)
    usage = {"sessions": {"size_bytes": session_bytes, "human_size": f"{session_bytes / (1024*1024):.2f} MB"}}
    total_bytes = session_bytes

    for category, directory in {**WORKSPACE_DIRS, "tenants": str(TENANTS_DIR)}.items():
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
