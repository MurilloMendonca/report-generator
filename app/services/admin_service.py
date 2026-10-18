"""Account administration; never exposes report contents or credential material."""
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update

from app import db
from app.models.db import AuditEvent, AuthSession, LLMUsageEvent, Report, User
from app.services import auth_service, usage_service
from app.services.redaction import redact


def list_users(offset=0, limit=50):
    with db.transaction() as tx:
        reports = select(Report.owner_id.label("owner"), func.count().label("reports"),
                         func.sum(Report.storage_bytes).label("storage_bytes")).group_by(Report.owner_id).subquery()
        usage = select(LLMUsageEvent.user_id.label("owner"), func.count().label("calls"),
                       func.sum(func.coalesce(LLMUsageEvent.input_tokens, 0) + func.coalesce(LLMUsageEvent.output_tokens, 0)).label("tokens")
                       ).where(LLMUsageEvent.created_at >= usage_service.month_start()).group_by(LLMUsageEvent.user_id).subquery()
        rows = tx.execute(select(User, reports.c.reports, reports.c.storage_bytes, usage.c.calls, usage.c.tokens)
                          .outerjoin(reports, reports.c.owner == User.id).outerjoin(usage, usage.c.owner == User.id)
                          .order_by(User.created_at.desc(), User.id).offset(offset).limit(limit)).all()
        total = tx.execute(select(func.count()).select_from(User)).scalar_one()
        return {"total": total, "offset": offset, "users": [{**auth_service.user_payload(user),
            "status": user.status, "reports": count or 0, "storage_bytes": int(storage or 0),
            "monthly_calls": calls or 0, "monthly_tokens": int(tokens or 0),
            "monthly_token_limit": user.monthly_token_limit, "report_call_limit": user.report_call_limit,
            "effective_limits": usage_service.limits(user)} for user, count, storage, calls, tokens in rows]}


def update_user(actor, target_id, changes, ip=None):
    with db.transaction() as tx:
        # The same lock protects account deletion, so two admins cannot remove
        # each other concurrently and leave the installation without an admin.
        tx.execute(text("SELECT pg_advisory_xact_lock(73482619)"))
        user = tx.get(User, target_id, with_for_update=True)
        if user is None:
            raise auth_service.AuthError("user_not_found", "Conta não encontrada.", 404)
        if changes.get("status") == "disabled":
            if str(actor.id) == str(user.id):
                raise auth_service.AuthError("self_disable", "Use outra conta de administrador para desativar esta conta.", 409)
            if user.role == "admin" and user.status == "active":
                admins = tx.execute(select(func.count()).select_from(User).where(User.role == "admin", User.status == "active")).scalar_one()
                if admins <= 1:
                    raise auth_service.AuthError("last_admin", "Mantenha pelo menos um administrador ativo.", 409)
            tx.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                       .values(revoked_at=datetime.now(timezone.utc)))
        for name, value in changes.items():
            setattr(user, name, value)
        user.updated_at = datetime.now(timezone.utc)
        auth_service.audit("admin_user_updated", actor.id, ip, tx=tx, target_id=str(user.id), changes=changes)
    return {"status": "OK"}


def audit_events(before=None, limit=50):
    with db.transaction() as tx:
        query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
        if before is not None:
            query = query.where(AuditEvent.id < before)
        events = list(tx.execute(query).scalars())
        return {"events": [{"id": event.id, "user_id": str(event.user_id) if event.user_id else None,
            "event": event.event, "ip": event.ip, "details": redact(event.details),
            "created_at": event.created_at.isoformat()} for event in events],
            "next_before": events[-1].id if len(events) == limit else None}
