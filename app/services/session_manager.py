"""Report sessions, stored as one row per report in the `reports` table.

The session document lives in `reports.state` (JSONB). Every change reads the
row with `SELECT ... FOR UPDATE`, applies the change, normalizes the document
and writes it back in the same transaction, so concurrent pipeline threads and
requests never lose each other's updates.
"""
import logging
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm.attributes import flag_modified

from app import db
from app.models.db import Report
from app.models.session import normalize_session

logger = logging.getLogger(__name__)

JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TERMINAL_STEPS = {"", "completed", "failed", "awaiting_clarification", "translation_failed"}
_NO_WRITE = object()


def is_valid_job_id(job_id) -> bool:
    return isinstance(job_id, str) and JOB_ID_PATTERN.fullmatch(job_id) is not None


def validate_job_id(job_id) -> str:
    """Reject ids that could escape a report folder or match other reports' files."""
    if not is_valid_job_id(job_id):
        raise ValueError(f"Invalid session id: {job_id!r}")
    return job_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def report_columns(state: dict) -> dict:
    """The queryable copies of a few session fields."""
    settings = state.get("settings") if isinstance(state.get("settings"), dict) else {}
    title = settings.get("title") or state.get("initial_prompt") or ""
    def text(value, limit=None):
        # Postgres text columns reject NUL characters (the JSONB copy is cleaned in app/db.py).
        return str(value or "").replace("\x00", "")[:limit]

    return {
        "title": text(title, 300),
        "status": text(state.get("status"), 1000),
        "current_step": text(state.get("current_step")),
        "pdf_version": int(state.get("pdf_version") or 0),
    }


def _normalize(state, job_id: str) -> dict | None:
    try:
        return normalize_session(state, job_id)
    except ValidationError:
        logger.exception("Stored session %s does not match the schema", job_id)
        return None


def _mutate(job_id: str, change, missing=None):
    """Lock the report row, call `change(session)`, and save the session unless
    `change` returns `_NO_WRITE`. Returns `change`'s result, or `missing` when
    the report doesn't exist."""
    if not is_valid_job_id(job_id):
        return missing
    with db.transaction() as tx:
        report = tx.get(Report, job_id, with_for_update=True)
        if report is None:
            return missing
        session = _normalize(report.state, job_id)
        if not session:
            return missing
        result = change(session)
        if result is _NO_WRITE:
            return missing
        session = normalize_session(session, job_id)
        report.state = session
        flag_modified(report, "state")
        for column, value in report_columns(session).items():
            setattr(report, column, value)
        report.updated_at = _now()
        return session if result is None else result


def create_session(job_id: str, data: dict, owner_id: uuid.UUID):
    """Create a report owned by `owner_id`, or replace the document of an
    existing report (whose owner never changes)."""
    validate_job_id(job_id)
    if owner_id is None:
        raise ValueError("A report needs an owner")
    data = normalize_session(data, job_id) or {}
    values = {"id": job_id, "state": data, "owner_id": owner_id, **report_columns(data)}
    statement = pg_insert(Report).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[Report.id],
        set_={**report_columns(data), "state": statement.excluded.state, "updated_at": func.now()},
    )
    with db.transaction() as tx:
        tx.execute(statement)


def get_session(job_id: str) -> dict:
    """The session document, whoever owns it. Request handlers must use
    get_owned_session instead; this is for background jobs and maintenance."""
    if not is_valid_job_id(job_id):
        return None
    with db.transaction() as tx:
        state = tx.execute(select(Report.state).where(Report.id == job_id)).scalar_one_or_none()
    return None if state is None else _normalize(state, job_id)


def get_owned_session(job_id: str, owner_id) -> dict | None:
    """The session document only if `owner_id` owns the report, else None."""
    if not is_valid_job_id(job_id) or owner_id is None:
        return None
    with db.transaction() as tx:
        state = tx.execute(
            select(Report.state).where(Report.id == job_id, Report.owner_id == owner_id)
        ).scalar_one_or_none()
    return None if state is None else _normalize(state, job_id)


def get_report_owner(job_id: str) -> uuid.UUID | None:
    if not is_valid_job_id(job_id):
        return None
    with db.transaction() as tx:
        return tx.execute(select(Report.owner_id).where(Report.id == job_id)).scalar_one_or_none()


def list_report_ids(owner_id) -> list[str]:
    with db.transaction() as tx:
        return list(tx.execute(select(Report.id).where(Report.owner_id == owner_id)).scalars())


def set_storage_bytes(job_id: str, size: int) -> None:
    with db.transaction() as tx:
        tx.execute(update(Report).where(Report.id == job_id).values(storage_bytes=max(0, int(size))))


def storage_used(owner_id) -> int:
    with db.transaction() as tx:
        return int(tx.execute(
            select(func.coalesce(func.sum(Report.storage_bytes), 0)).where(Report.owner_id == owner_id)
        ).scalar())


def reports_created_since(owner_id, since: datetime) -> int:
    with db.transaction() as tx:
        return int(tx.execute(
            select(func.count()).select_from(Report).where(Report.owner_id == owner_id, Report.created_at >= since)
        ).scalar())


def delete_session(job_id: str) -> bool:
    """Delete a report's row. Its files are removed by cleanup_service."""
    if not is_valid_job_id(job_id):
        return False
    with db.transaction() as tx:
        return tx.execute(delete(Report).where(Report.id == job_id)).rowcount > 0


def update_session(job_id: str, updates: dict):
    def change(session):
        session.update({"updated_at": _now_iso(), **updates})
    return _mutate(job_id, change)


def preserve_report_on_failure(refine):
    """Keep the last usable report when a refinement fails before a new PDF exists."""
    @wraps(refine)
    def guarded(job_id: str, *args, **kwargs):
        original = get_session(job_id) or {}
        fields = (
            "files", "data_filter", "data_preprocessing", "semantic_model",
            "document_insights", "artifacts", "artifact_validation", "template",
            "components", "calculated_metrics", "component_validation_retries",
            "settings", "script", "script_path", "pdf_path", "retry_count",
            "current_goal", "refinement_history", "pending_refinement",
        )
        snapshot = {key: deepcopy(original[key]) for key in fields if key in original}
        raised = False
        try:
            return refine(job_id, *args, **kwargs)
        except Exception:
            raised = True
            raise
        finally:
            latest = get_session(job_id) or {}
            if (
                original.get("pdf_version", 0) > 0
                and original.get("artifacts") and original.get("template")
                and (raised or latest.get("current_step") == "failed")
                and latest.get("pdf_version") == original.get("pdf_version")
            ):
                update_session(job_id, snapshot)
                add_log(job_id, "Refinamento falhou; o estado editável da última versão do relatório foi preservado.")
    return guarded


EXPLORE_PENDING_MESSAGE = "Analisando os dados e preparando a resposta..."


def has_pending_exploration(session: dict) -> bool:
    return any(
        message.get("role") == "assistant" and message.get("status") == "pending"
        for message in (session or {}).get("explore_history", [])
        if isinstance(message, dict)
    )


def begin_exploration_turn(job_id: str, user_message: dict, turn_id: str):
    """Append a question with a pending answer. Returns None if the session is gone,
    or False if another question is still pending."""
    busy = []

    def change(session):
        if has_pending_exploration(session):
            busy.append(True)
            return _NO_WRITE
        placeholder = {
            "role": "assistant", "content": EXPLORE_PENDING_MESSAGE, "turn_id": turn_id,
            "charts": [], "status": "pending", "created_at": _now_iso(),
        }
        session["explore_history"] = [*session.get("explore_history", []), user_message, placeholder]
        session["updated_at"] = _now_iso()

    result = _mutate(job_id, change)
    return False if busy else result


def finish_exploration_turn(job_id: str, turn_id: str, assistant_message: dict):
    """Replace a turn's pending answer with the final one, keeping concurrent updates."""
    def change(session):
        history = list(session.get("explore_history", []))
        for index, message in enumerate(history):
            if isinstance(message, dict) and message.get("role") == "assistant" and message.get("turn_id") == turn_id:
                history[index] = assistant_message
                break
        else:
            return _NO_WRITE
        session["explore_history"] = history
        session["updated_at"] = _now_iso()

    return _mutate(job_id, change)


def list_recent_sessions(owner_id, limit: int = 12) -> list:
    query = (
        select(
            Report.id,
            Report.state["initial_prompt"].astext,
            Report.status,
            func.jsonb_array_length(func.coalesce(Report.state["files"], func.cast("[]", JSONB))),
            Report.pdf_version,
            Report.state["created_at"].astext,
            Report.created_at,
            Report.updated_at,
        )
        .where(Report.owner_id == owner_id)
        .order_by(Report.updated_at.desc())
        .limit(limit)
    )
    with db.transaction() as tx:
        rows = tx.execute(query).all()
    return [
        {
            "job_id": job_id,
            "initial_prompt": initial_prompt or "",
            "status": status or "",
            "file_count": file_count or 0,
            "pdf_version": pdf_version or 0,
            "created_at": state_created_at or created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        }
        for job_id, initial_prompt, status, file_count, pdf_version, state_created_at, created_at, updated_at in rows
    ]


def list_session_activity() -> list[tuple[str, datetime]]:
    """(job_id, last update) for every report, used by retention cleanup."""
    with db.transaction() as tx:
        return [tuple(row) for row in tx.execute(select(Report.id, Report.updated_at)).all()]


def add_log(job_id: str, message: str):
    def change(session):
        timestamp = datetime.now().strftime("%H:%M:%S")
        session["logs"] = [*session.get("logs", []), f"[{timestamp}] {message}"]
        session["status"] = message
        session["updated_at"] = _now_iso()
        return True

    _mutate(job_id, change)


def append_stage_event(
    job_id: str,
    stage: str,
    event: str,
    *,
    status: str | None = None,
    duration_ms: float | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    details: dict | None = None,
):
    def change(session):
        session["stage_history"] = [*session.get("stage_history", []), {
            "stage": stage,
            "event": event,
            "status": status or session.get("status", ""),
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            "provider": provider,
            "model_name": model_name,
            "details": details or {},
            "timestamp": _now_iso(),
        }]
        session["updated_at"] = _now_iso()
        return status or session.get("status", "")

    stored_status = _mutate(job_id, change)
    if stored_status is None:
        return
    logger.info(
        "stage_event job_id=%s stage=%s event=%s status=%s duration_ms=%s provider=%s model=%s",
        job_id,
        stage,
        event,
        stored_status,
        round(duration_ms, 1) if duration_ms is not None else "",
        provider or "",
        model_name or "",
    )


def record_llm_usage(
    job_id: str,
    operation: str,
    provider: str,
    model_name: str,
    *,
    attempt: int,
    duration_ms: float,
    status: str,
    prompt_chars: int = 0,
    response_chars: int = 0,
    error: str | None = None,
    cache_hit: bool = False,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
):
    def change(session):
        usage = session.get("llm_usage", {})
        for key, default in (
            ("calls", 0), ("attempts", 0), ("duration_ms", 0.0), ("by_provider", {}),
            ("by_operation", {}), ("history", []), ("cache_hits", 0),
            ("input_tokens", 0), ("output_tokens", 0),
        ):
            usage.setdefault(key, default)
        usage["calls"] += 1
        usage["input_tokens"] += int(input_tokens or 0)
        usage["output_tokens"] += int(output_tokens or 0)
        usage["attempts"] += int(attempt)
        usage["duration_ms"] = round(float(usage["duration_ms"]) + float(duration_ms), 1)
        usage["by_provider"][provider] = usage["by_provider"].get(provider, 0) + 1
        usage["by_operation"][operation] = usage["by_operation"].get(operation, 0) + 1
        if cache_hit:
            usage["cache_hits"] += 1
        usage["last_provider"] = provider
        usage["last_model_name"] = model_name
        usage["last_operation"] = operation
        usage["last_status"] = status
        usage["last_duration_ms"] = round(duration_ms, 1)
        usage["last_error"] = error or ""
        usage["history"] = [*usage["history"], {
            "operation": operation,
            "provider": provider,
            "model_name": model_name,
            "attempt": attempt,
            "duration_ms": round(duration_ms, 1),
            "status": status,
            "prompt_chars": int(prompt_chars),
            "response_chars": int(response_chars),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_hit": bool(cache_hit),
            "error": error or "",
            "timestamp": _now_iso(),
        }][-50:]
        session["llm_usage"] = usage
        session["updated_at"] = _now_iso()
        return True

    if _mutate(job_id, change) is None:
        return
    logger.info(
        "llm_usage job_id=%s operation=%s provider=%s model=%s attempt=%s duration_ms=%s status=%s cache_hit=%s",
        job_id,
        operation,
        provider,
        model_name,
        attempt,
        round(duration_ms, 1),
        status,
        cache_hit,
    )


def reserve_session_run(job_id: str, source: str = "pipeline") -> dict:
    def change(session):
        previous_run_id = int(session.get("active_run_id", 0) or 0)
        next_run_id = int(session.get("run_sequence", 0) or 0) + 1
        session["run_history"] = [*session.get("run_history", []), {
            "run_id": next_run_id,
            "source": source,
            "previous_run_id": previous_run_id,
            "timestamp": _now_iso(),
            "status": "active",
        }][-50:]
        session["run_sequence"] = next_run_id
        session["active_run_id"] = next_run_id
        session["active_run_source"] = source
        session["updated_at"] = _now_iso()
        return {"run_id": next_run_id, "previous_run_id": previous_run_id}

    reserved = _mutate(job_id, change, missing={"run_id": 0, "previous_run_id": 0})
    if reserved["run_id"]:
        logger.info(
            "run_reserved job_id=%s run_id=%s previous_run_id=%s source=%s",
            job_id,
            reserved["run_id"],
            reserved["previous_run_id"],
            source,
        )
    return reserved


def is_current_session_run(job_id: str, run_id: int | None) -> bool:
    if not run_id:
        return False
    session = get_session(job_id)
    if not session:
        return False
    return int(session.get("active_run_id", 0) or 0) == int(run_id)


def set_session_stage(job_id: str, step: str, progress: int = None, error: dict = None):
    updates = {"current_step": step}
    if progress is not None:
        updates["progress"] = max(0, min(100, int(progress)))
    if error is not None:
        updates["error"] = error
        if error:
            updates["status"] = error.get("message", "Erro")
    return update_session(job_id, updates)


def recover_interrupted_sessions() -> list[str]:
    """Mark sessions left mid-run by a server restart as failed, so the UI stops waiting."""
    pending_turn = Report.state["explore_history"].contains([{"role": "assistant", "status": "pending"}])
    with db.transaction() as tx:
        job_ids = tx.execute(
            select(Report.id).where(or_(Report.current_step.not_in(TERMINAL_STEPS), pending_turn))
        ).scalars().all()

    recovered = []
    message = "O processamento foi interrompido porque o servidor reiniciou. Tente novamente."
    for job_id in job_ids:
        session = get_session(job_id)
        if not session:
            continue
        if has_pending_exploration(session):
            explore_message = "A consulta foi interrompida porque o servidor reiniciou. Tente novamente."
            history = [
                {**item, "content": explore_message, "status": "error", "error_status": 503}
                if isinstance(item, dict) and item.get("role") == "assistant" and item.get("status") == "pending"
                else item
                for item in session.get("explore_history", [])
            ]
            update_session(job_id, {"explore_history": history})
            recovered.append(job_id)
        if session.get("current_step", "") in TERMINAL_STEPS:
            continue
        update_session(job_id, {
            "current_step": "failed",
            "progress": 100,
            "status": message,
            "active_run_id": 0,
            "error": {"code": "interrupted", "message": message, "stage": session.get("current_step", "")},
        })
        add_log(job_id, message)
        if job_id not in recovered:
            recovered.append(job_id)
    if recovered:
        logger.warning("Recovered %d interrupted session(s): %s", len(recovered), ", ".join(recovered))
    return recovered
