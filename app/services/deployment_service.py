"""Local readiness checks. External policies and backups need a host drill."""
import os
from pathlib import Path

from sqlalchemy import select

from app import db
from app.models.db import Report


def production():
    return os.getenv("APP_ENV", "development").lower() not in {"development", "dev", "local", "test"}


def validate_runtime_configuration():
    if not production():
        return
    from app.security import COOKIE_SECURE, signup_open
    from app.services import email_service, turnstile
    if not COOKIE_SECURE or os.getenv("PDF_REPORT_ALLOW_UNSAFE_SANDBOX") == "1":
        raise RuntimeError("Production requires Secure cookies and the bubblewrap sandbox.")
    if signup_open() and (email_service.backend() != "smtp" or not turnstile.enabled()):
        raise RuntimeError("Open production sign-up requires SMTP and Turnstile.")
    if email_service.backend() == "smtp" and os.getenv("SMTP_PORT", "587") != "465" and os.getenv("SMTP_STARTTLS", "1") != "1":
        raise RuntimeError("Production SMTP requires TLS.")
    if email_service.backend() == "smtp" and not os.getenv("SMTP_FROM", "").strip():
        raise RuntimeError("Production SMTP requires SMTP_FROM.")


def launch_check():
    from app.services import email_service, turnstile
    from app.services.crypto import validate_configuration
    from app.services.executor_service import sandbox_status
    from app.services.cloudflare_access import configuration
    from app.utils.report_paths import ReportPaths
    checks = []
    for name, check in (("runtime_configuration", validate_runtime_configuration), ("encryption_configuration", validate_configuration)):
        try:
            check()
            checks.append({"check": name, "ok": True})
        except (RuntimeError, ValueError):
            checks.append({"check": name, "ok": False})
    status = sandbox_status()
    checks.append({"check": "bubblewrap", "ok": bool(status["bubblewrap_available"]) and not status["unsafe_fallback_allowed"]})
    checks.append({"check": "smtp", "ok": email_service.backend() == "smtp" and bool(os.getenv("SMTP_FROM", "").strip())})
    checks.append({"check": "turnstile", "ok": turnstile.enabled()})
    checks.append({"check": "cloudflare_access", "ok": configuration()[2]})
    key_file = os.getenv("APP_ENCRYPTION_KEYS_FILE", "").strip()
    checks.append({"check": "keyring_outside_backups", "ok": not key_file or not any(
        Path(key_file).resolve().is_relative_to(Path(folder).resolve()) for folder in ("workspace", "backups")
    )})
    with db.transaction() as tx:
        rows = tx.execute(select(Report.id, Report.owner_id, Report.state["storage_dir"].astext)).all()
    invalid = 0
    for job_id, owner_id, storage_dir in rows:
        expected = ReportPaths.for_new_report(job_id, str(owner_id)).root.absolute()
        if not storage_dir or Path(storage_dir).absolute() != expected or Path(storage_dir).resolve() != expected:
            invalid += 1
    checks.append({"check": "isolated_report_storage", "ok": invalid == 0, "reports_needing_migration": invalid})
    return {"ready": production() and all(check["ok"] for check in checks), "production": production(), "checks": checks}
