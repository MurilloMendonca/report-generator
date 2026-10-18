"""Per-user limits (plan 7.2): storage, reports per day, and how many jobs run at once.

Defaults come from the plan; each is an environment variable.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from app.services import session_manager
from app.utils.report_paths import ReportPaths

STORAGE_BYTES = int(os.getenv("PDF_REPORT_QUOTA_STORAGE_BYTES", str(10 * 1024 ** 3)))
REPORTS_PER_DAY = int(os.getenv("PDF_REPORT_QUOTA_REPORTS_PER_DAY", "50"))
JOBS_PER_USER = max(1, int(os.getenv("PDF_REPORT_MAX_JOBS_PER_USER", "1")))


def _size_of(root: Path) -> int:
    total = 0
    for item in root.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def refresh_storage(job_id: str, session: dict | None = None) -> int:
    """Measure a report's folder and store the size. Legacy shared folders count as 0."""
    session = session or session_manager.get_session(job_id)
    if not session:
        return 0
    try:
        paths = ReportPaths.from_session(session)
    except ValueError:
        return 0
    size = 0 if paths.is_legacy or not paths.root.is_dir() else _size_of(paths.root)
    session_manager.set_storage_bytes(job_id, size)
    return size


def check_storage(owner_id, incoming_bytes: int) -> None:
    used = session_manager.storage_used(owner_id)
    if used + max(0, incoming_bytes) > STORAGE_BYTES:
        raise HTTPException(status_code=413, detail={
            "code": "storage_quota",
            "message": (
                f"Seu espaço acabou: você usa {used / 1024 ** 3:.1f} GB de {STORAGE_BYTES / 1024 ** 3:.0f} GB. "
                "Exclua relatórios antigos para liberar espaço."
            ),
        })


def check_daily_reports(owner_id) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    if session_manager.reports_created_since(owner_id, since) >= REPORTS_PER_DAY:
        raise HTTPException(status_code=429, detail={
            "code": "daily_report_limit",
            "message": f"Você atingiu o limite de {REPORTS_PER_DAY} relatórios em 24 horas. Tente novamente mais tarde.",
        })


def upload_size(files) -> int:
    return sum(int(getattr(file, "size", 0) or 0) for file in files or [])


def usage_summary(owner_id) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    return {
        "storage_bytes": session_manager.storage_used(owner_id),
        "storage_limit_bytes": STORAGE_BYTES,
        "reports_last_24h": session_manager.reports_created_since(owner_id, since),
        "reports_per_day_limit": REPORTS_PER_DAY,
        "jobs_per_user": JOBS_PER_USER,
    }
