"""Caps how many report jobs run at once, so a burst of requests can't exhaust the worker threads."""
import logging
import os
import threading

from app.services.session_manager import add_log

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = max(1, int(os.getenv("PDF_REPORT_MAX_CONCURRENT_JOBS", "2")))
_job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)


def run_job(func, job_id: str, *args, **kwargs):
    """Run a background job (pipeline, refinement, render) once a slot is free.

    Only wrap the entry point: nested calls inside a job must not wait for a slot.
    """
    if not _job_slots.acquire(blocking=False):
        add_log(job_id, "Aguardando outros relatórios terminarem...")
        _job_slots.acquire()
    try:
        return func(job_id, *args, **kwargs)
    except Exception:
        logger.exception("Background job failed job_id=%s func=%s", job_id, getattr(func, "__name__", func))
    finally:
        _job_slots.release()
