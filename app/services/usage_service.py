"""Durable provider attempts and quotas. No prompts, responses, keys or SDK errors."""
import os
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from app.models.db import LLMUsageEvent, Report, User
from app.services.llm.errors import LLMUsageLimitError
from app.services.redaction import redact


def month_start():
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def limits(user):
    return {
        "monthly_token_limit": user.monthly_token_limit if user.monthly_token_limit is not None else max(0, int(os.getenv("PDF_REPORT_MONTHLY_TOKEN_LIMIT", "0"))),
        "report_call_limit": user.report_call_limit if user.report_call_limit is not None else max(0, int(os.getenv("PDF_REPORT_REPORT_CALL_LIMIT", "0"))),
    }


def reserve(user_id, report_id, provider, model, operation):
    """Serialize admission per owner so simultaneous calls cannot evade call caps.

    Monthly limits stop new attempts after *reported* tokens reach the cap.
    Already admitted requests can exceed it; unknown token counts stay unknown.
    """
    with db.transaction() as tx:
        if report_id:
            owner = tx.execute(select(Report.owner_id).where(Report.id == report_id)).scalar_one_or_none()
            if owner is None or str(owner) != str(user_id):
                raise LLMUsageLimitError(operation, "account_unavailable", 0)
        user = tx.get(User, user_id, with_for_update=True)
        if user is None or user.status != "active":
            raise LLMUsageLimitError(operation, "account_disabled", 0)
        caps = limits(user)
        if caps["monthly_token_limit"]:
            used = tx.execute(select(func.coalesce(func.sum(
                func.coalesce(LLMUsageEvent.input_tokens, 0) + func.coalesce(LLMUsageEvent.output_tokens, 0)
            ), 0)).where(LLMUsageEvent.user_id == user.id, LLMUsageEvent.created_at >= month_start())).scalar_one()
            if used >= caps["monthly_token_limit"]:
                raise LLMUsageLimitError(operation, "monthly_token_limit", caps["monthly_token_limit"])
        if report_id and caps["report_call_limit"]:
            calls = tx.execute(select(func.count()).select_from(LLMUsageEvent).where(
                LLMUsageEvent.report_id == report_id, LLMUsageEvent.cache_hit.is_(False)
            )).scalar_one()
            if calls >= caps["report_call_limit"]:
                raise LLMUsageLimitError(operation, "report_call_limit", caps["report_call_limit"])
        event = LLMUsageEvent(user_id=user.id, report_id=report_id, provider=redact(provider), model=redact(model), operation=redact(operation))
        tx.add(event)
        tx.flush()
        return event.id


def record(report_id, operation, provider, model, *, event_id=None, status, duration_ms,
           input_tokens=None, output_tokens=None, cache_hit=False, served_model=None,
           fallback_used=False, error_code=None):
    with db.transaction() as tx:
        event = tx.get(LLMUsageEvent, event_id, with_for_update=True) if event_id else None
        if event_id and event is None:  # account deleted while request was in flight
            return
        if event is None:
            owner = tx.execute(select(Report.owner_id).where(Report.id == report_id)).scalar_one_or_none()
            if owner is None:
                return
            event = LLMUsageEvent(user_id=owner, report_id=report_id, operation=redact(operation), provider=redact(provider), model=redact(model))
            tx.add(event)
        elif event.status != "pending":
            return  # idempotent completion
        event.status = status
        event.duration_ms = max(0, round(duration_ms))
        event.input_tokens = max(0, int(input_tokens)) if input_tokens is not None else None
        event.output_tokens = max(0, int(output_tokens)) if output_tokens is not None else None
        event.cache_hit = bool(cache_hit)
        event.served_model = redact(served_model)
        event.fallback_used = bool(fallback_used)
        # Only stable application codes, never arbitrary exception messages.
        event.error_code = error_code if error_code in {"provider_error", "timeout", "invalid_provider_key", "model_refusal", "llm_contract_error"} else None


def _totals(tx, *conditions):
    row = tx.execute(select(
        func.count().label("calls"),
        func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LLMUsageEvent.duration_ms), 0).label("duration_ms"),
        func.count().filter(LLMUsageEvent.status == "pending").label("pending_calls"),
        func.count().filter(LLMUsageEvent.status.not_in(["ok", "pending"])).label("failed_calls"),
        func.count().filter(LLMUsageEvent.input_tokens.is_(None) | LLMUsageEvent.output_tokens.is_(None)).label("unknown_token_calls"),
    ).where(*conditions)).mappings().one()
    return {key: int(value) for key, value in row.items()}


def account_summary(user_id=None):
    conditions = [LLMUsageEvent.created_at >= month_start()]
    if user_id is not None:
        conditions.append(LLMUsageEvent.user_id == user_id)
    with db.transaction() as tx:
        summary = _totals(tx, *conditions)
        summary["month_start"] = month_start().isoformat()
        summary["by_provider"] = [{"provider": provider, "calls": calls, "input_tokens": int(inputs or 0), "output_tokens": int(outputs or 0)}
            for provider, calls, inputs, outputs in tx.execute(select(LLMUsageEvent.provider, func.count(),
                func.sum(LLMUsageEvent.input_tokens), func.sum(LLMUsageEvent.output_tokens)
            ).where(*conditions).group_by(LLMUsageEvent.provider).order_by(LLMUsageEvent.provider))]
        if user_id is not None:
            summary.update(limits(tx.get(User, user_id)))
        return summary


def report_summary(report_id):
    with db.transaction() as tx:
        where = LLMUsageEvent.report_id == report_id
        summary = _totals(tx, where)
        if not summary["calls"]:
            return None  # historical reports retain their pre-event summary
        summary["attempts"] = summary["calls"]
        for field, column in (("by_provider", LLMUsageEvent.provider), ("by_operation", LLMUsageEvent.operation)):
            summary[field] = dict(tx.execute(select(column, func.count()).where(where).group_by(column)).all())
        summary["cache_hits"] = tx.execute(select(func.count()).select_from(LLMUsageEvent).where(where, LLMUsageEvent.cache_hit)).scalar_one()
        events = list(tx.execute(select(LLMUsageEvent).where(where).order_by(LLMUsageEvent.id.desc()).limit(50)).scalars())
        summary["history"] = [{"provider": e.provider, "model_name": e.model, "operation": e.operation,
            "status": e.status, "duration_ms": e.duration_ms, "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens, "served_model": e.served_model, "fallback_used": e.fallback_used,
            "cache_hit": e.cache_hit, "error": e.error_code or "", "timestamp": e.created_at.isoformat()} for e in reversed(events)]
        last = events[0]
        summary.update(last_provider=last.provider, last_model_name=last.model, last_operation=last.operation,
                       last_status=last.status, last_duration_ms=last.duration_ms, last_error=last.error_code or "")
        fallback = next((event for event in events if event.fallback_used), None)
        if fallback:
            summary["last_fallback"] = {"provider": fallback.provider, "requested_model": fallback.model, "served_model": fallback.served_model}
        return summary


def recent_reports(user_id, limit=50):
    with db.transaction() as tx:
        rows = tx.execute(select(Report.id, Report.title).where(Report.owner_id == user_id)
                          .order_by(Report.updated_at.desc()).limit(limit)).all()
    return [{"job_id": job_id, "title": title, "usage": report_summary(job_id)} for job_id, title in rows]


def recover_pending():
    """After a restart keep unknown costs explicit and keep attempts in call caps."""
    from sqlalchemy import update
    with db.transaction() as tx:
        tx.execute(update(LLMUsageEvent).where(LLMUsageEvent.status == "pending").values(status="interrupted"))
