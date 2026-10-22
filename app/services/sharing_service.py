"""Owner-managed, revocable read-only links to the live report workspace."""
import hashlib
import os
import re
import secrets
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app import db
from app.models.db import Report, ReportShareLink, User
from app.services.component_service import component_texts
from app.services.redaction import redact
from app.services.session_manager import get_session
from app.utils.report_paths import ReportPaths


SHARED_HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow, noarchive"}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def _now():
    return datetime.now(timezone.utc)


def _summary(link):
    return {"id": str(link.id), "label": link.label, "created_at": link.created_at.isoformat(),
            "expires_at": link.expires_at.isoformat() if link.expires_at else None,
            "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
            "active": link.revoked_at is None and (link.expires_at is None or link.expires_at > _now())}


def create_share_link(job_id: str, owner_id, label: str, expires_in_hours: int | None):
    token = secrets.token_urlsafe(32)
    with db.transaction() as tx:
        report = tx.execute(select(Report).where(Report.id == job_id, Report.owner_id == owner_id).with_for_update()).scalar_one_or_none()
        if report is None:
            raise HTTPException(404, detail="Relatório não encontrado.")
        link = ReportShareLink(report_id=job_id, token_hash=hashlib.sha256(token.encode()).digest(), label=label.strip(),
                               expires_at=_now() + timedelta(hours=expires_in_hours) if expires_in_hours is not None else None)
        tx.add(link)
        tx.flush()
        return {**_summary(link), "path": f"/share/{token}"}


def list_share_links(job_id: str, owner_id):
    with db.transaction() as tx:
        links = tx.execute(select(ReportShareLink).join(Report).where(Report.id == job_id, Report.owner_id == owner_id)
                           .order_by(ReportShareLink.created_at.desc())).scalars().all()
        return [_summary(link) for link in links]


def revoke_share_link(job_id: str, owner_id, link_id: str):
    try:
        identifier = uuid.UUID(link_id)
    except ValueError:
        raise HTTPException(404, detail="Link não encontrado.") from None
    with db.transaction() as tx:
        link = tx.execute(select(ReportShareLink).join(Report).where(ReportShareLink.id == identifier, Report.id == job_id,
                          Report.owner_id == owner_id).with_for_update(of=ReportShareLink)).scalar_one_or_none()
        if link is None:
            raise HTTPException(404, detail="Link não encontrado.")
        if link.revoked_at is None:
            link.revoked_at = _now()
        return _summary(link)


def require_shared_report(token: str):
    unavailable = HTTPException(404, detail="Este link expirou, foi revogado ou não está disponível.")
    if not TOKEN_PATTERN.fullmatch(token):
        raise unavailable
    with db.transaction() as tx:
        link = tx.execute(select(ReportShareLink).join(Report).join(User, Report.owner_id == User.id).where(
            ReportShareLink.token_hash == hashlib.sha256(token.encode()).digest(), ReportShareLink.revoked_at.is_(None), User.status == "active",
        )).scalar_one_or_none()
        if link is None or (link.expires_at is not None and link.expires_at <= _now()):
            raise unavailable
        job_id = link.report_id
        expiry = link.expires_at.isoformat() if link.expires_at else None
    session = get_session(job_id)
    if session is None:
        raise unavailable
    return session, {"expires_at": expiry, "read_only": True}


def shared_report_payload(session: dict, sharing: dict):
    """Expose report content, excluding account, connector, usage, and host-path data."""
    fields = ("job_id", "created_at", "updated_at", "status", "current_step", "progress", "pdf_version", "initial_prompt", "current_goal",
              "artifacts", "template", "components", "calculated_metrics", "settings", "provider", "model_name", "script", "logs",
              "refinement_messages", "refinement_history", "explore_history", "document_insights", "semantic_model", "render_summary", "quality_review", "data_preprocessing", "data_filter")
    payload = {key: deepcopy(session.get(key)) for key in fields}
    payload["component_texts"] = component_texts(session)
    payload["sharing"] = sharing
    payload["has_pdf"] = bool(session.get("pdf_path"))
    payload["files"] = [{key: deepcopy(file.get(key)) for key in ("original_filename", "mime_type", "size_bytes", "artifact", "raw_artifact", "validation", "preprocessing_description", "filter_description", "preprocessing_impact", "filter_impact")}
                        | {"has_preprocessed": bool(file.get("preprocessed_path") or file.get("filtered_path"))} for file in session.get("files", [])]
    payload["source_snapshot_history"] = [
        {"original_filename": file.get("original_filename"), **{key: file.get("source", {}).get(key) for key in ("snapshot_id", "dataset_name", "row_count", "fetched_at")}}
        for file in session.get("source_snapshot_history", []) if file.get("source", {}).get("snapshot_id")
    ]
    replacements = {}
    for index, file in enumerate(session.get("files", [])):
        for key in ("path", "preprocessed_path", "filtered_path"):
            if file.get(key):
                replacements[file[key]] = f"/report/files/{index}/{os.path.basename(file[key])}"
                replacements[os.path.abspath(file[key])] = replacements[file[key]]
    paths = ReportPaths.from_session(session)
    for folder in (paths.uploads, paths.filtered, paths.scripts, paths.output):
        replacements[str(folder)] = f"/report/{folder.name}"
        replacements[str(folder.absolute())] = f"/report/{folder.name}"
    if not paths.is_legacy:
        replacements[str(paths.root)] = "/report"
        replacements[str(paths.root.absolute())] = "/report"
    ordered_replacements = sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True)
    def clean(value):
        if isinstance(value, str):
            for original, display in ordered_replacements:
                value = value.replace(original, display)
            return value
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value
    # A settings allowlist also protects imported legacy sessions with extra fields.
    from app.models.session import ReportSettings
    payload["settings"] = {key: value for key, value in (payload.get("settings") or {}).items() if key in ReportSettings.model_fields}
    return redact(clean(payload))
