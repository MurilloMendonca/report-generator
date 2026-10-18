"""Caps how many report jobs run at once: globally, so a burst of requests can't
exhaust the worker threads or the Mac's memory, and per user, so one person
can't take every slot."""
import logging
import os
import threading
from collections import defaultdict

from app.services import quotas
from app.services.session_manager import add_log, get_report_owner

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = max(1, int(os.getenv("PDF_REPORT_MAX_CONCURRENT_JOBS", "2")))
_job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
_user_slots: dict = defaultdict(lambda: threading.BoundedSemaphore(quotas.JOBS_PER_USER))
_user_slots_guard = threading.Lock()


def _user_slot(owner_id):
    with _user_slots_guard:
        return _user_slots[owner_id]


def run_job(func, job_id: str, *args, **kwargs):
    """Run a background job (pipeline, refinement, render) once a slot is free.

    The owner's slot is taken first, then a global one, so a user waiting on
    their own earlier report never holds a global slot. Only wrap the entry
    point: nested calls inside a job must not wait for a slot.
    """
    owner_id = get_report_owner(job_id)
    user_slot = _user_slot(owner_id) if owner_id is not None else None
    if user_slot is not None and not user_slot.acquire(blocking=False):
        add_log(job_id, "Aguardando seu relatório anterior terminar...")
        user_slot.acquire()
    try:
        if not _job_slots.acquire(blocking=False):
            add_log(job_id, "Aguardando outros relatórios terminarem...")
            _job_slots.acquire()
        try:
            if owner_id is not None:
                from app.services.auth_service import get_user
                from app.services.session_manager import fail_disabled_account_job
                user = get_user(owner_id)
                if user is None or user.status != "active":
                    fail_disabled_account_job(job_id)
                    return
            return func(job_id, *args, **kwargs)
        except Exception:
            logger.exception("Background job failed job_id=%s func=%s", job_id, getattr(func, "__name__", func))
        finally:
            _job_slots.release()
            try:
                quotas.refresh_storage(job_id)
            except Exception:
                logger.exception("Could not measure storage job_id=%s", job_id)
    finally:
        if user_slot is not None:
            user_slot.release()
