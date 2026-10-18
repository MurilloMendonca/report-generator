import logging
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from app.services.executor_service import SandboxUnavailableError, execute_script
from app.services.llm.errors import LLMBudgetError, LLMContractError, LLMTimeoutError
from app.services.llm.script import generate_exploration_script
from app.services.pipeline_service import build_files_map, metadata_json
from app.services.session_manager import begin_exploration_turn, finish_exploration_turn, get_session
from app.utils.report_paths import ReportPaths
from app.utils.exploration_charts import (
    MAX_ANSWER_CHARS, collect_exploration_charts, exploration_directory,
)


logger = logging.getLogger(__name__)
_locks = {}
_locks_guard = threading.Lock()


class ExplorationError(RuntimeError):
    def __init__(self, message: str, status_code: int, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = {"message": message, **(details or {})}


@contextmanager
def exploration_lock(job_id: str):
    with _locks_guard:
        lock = _locks.setdefault(job_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ExplorationError("Já existe uma consulta em andamento nesta sessão. Aguarde a resposta.", 409)
    try:
        yield
    finally:
        lock.release()


BUSY_MESSAGE = "Já existe uma consulta em andamento nesta sessão. Aguarde a resposta."


def _exploration_running(job_id: str) -> bool:
    with _locks_guard:
        lock = _locks.get(job_id)
    return bool(lock and lock.locked())


def start_exploration(job_id: str, prompt: str) -> dict:
    """Validate a question and record it with a pending answer. Raises ExplorationError."""
    prompt = (prompt or "").strip()
    if not prompt or len(prompt) > 6000:
        raise ExplorationError("A consulta deve conter entre 1 e 6000 caracteres.", 400)
    turn_id = uuid.uuid4().hex
    try:
        exploration_directory(job_id, turn_id)
    except ValueError as exc:
        raise ExplorationError("Sessão não encontrada.", 404) from exc
    if _exploration_running(job_id):
        raise ExplorationError(BUSY_MESSAGE, 409)
    session = begin_exploration_turn(job_id, {
        "role": "user", "content": prompt, "turn_id": turn_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, turn_id)
    if session is None:
        raise ExplorationError("Sessão não encontrada.", 404)
    if session is False:
        raise ExplorationError(BUSY_MESSAGE, 409)
    return {"turn_id": turn_id, "status": "pending", "history": session["explore_history"]}


def complete_exploration(job_id: str, turn_id: str, prompt: str) -> dict:
    """Run a started question and store its answer in place of the pending one.

    Returns the response on success; raises ExplorationError (with the stored
    history in its details) on failure. The stored turn is resolved either way.
    """
    output_directory = None
    answer, charts, error = "", [], None
    try:
        with exploration_lock(job_id):
            session = get_session(job_id)
            if not session:
                raise ExplorationError("Sessão não encontrada.", 404)
            report_paths = ReportPaths.from_session(session).ensure()
            output_directory = exploration_directory(job_id, turn_id, report_paths.output)
            history = [
                message for message in session.get("explore_history", [])
                if not (isinstance(message, dict) and message.get("turn_id") == turn_id)
            ]
            output_directory.mkdir(parents=True, exist_ok=False)
            script = generate_exploration_script(
                metadata_json(session), prompt, build_files_map(session),
                session.get("provider"), session.get("model_name"),
                history=history, job_id=job_id,
                settings=session.get("settings"), output_directory=str(output_directory),
            )
            result = execute_script(script, os.path.join(report_paths.scripts, f"explore_{job_id}_{turn_id}.py"), paths=report_paths)
            if not result.get("success"):
                logger.warning("Exploration execution failed job_id=%s turn_id=%s stderr=%s", job_id, turn_id, str(result.get("stderr") or "")[-1500:])
                raise ExplorationError("Não foi possível executar a consulta. Revise as colunas e tente novamente.", 422)
            answer = str(result.get("stdout") or "")
            if len(answer) > MAX_ANSWER_CHARS:
                raise ValueError("Exploration answer exceeds the text size limit")
            charts = collect_exploration_charts(job_id, turn_id, report_paths.output)
            if not answer.strip() and not charts:
                raise ValueError("Exploration produced no answer or charts")
    except SandboxUnavailableError:
        logger.error("Exploration blocked: sandbox unavailable job_id=%s", job_id)
        error = ExplorationError("O ambiente seguro de execução não está disponível no servidor.", 503)
    except LLMBudgetError:
        error = ExplorationError("O limite de chamadas do modelo foi atingido. Revise as configurações e tente novamente.", 429)
    except LLMTimeoutError:
        error = ExplorationError("O modelo demorou demais para responder. Tente novamente ou simplifique a consulta.", 504)
    except ExplorationError as exc:
        error = exc
    except (ValueError, OSError, LLMContractError) as exc:
        logger.warning("Invalid exploration output job_id=%s turn_id=%s error=%s", job_id, turn_id, str(exc))
        error = ExplorationError("A resposta ou o gráfico gerado não é válido. Tente reformular a consulta.", 422)
    except Exception:
        logger.exception("Exploration failed job_id=%s turn_id=%s", job_id, turn_id)
        error = ExplorationError("Não foi possível concluir a consulta. Tente novamente.", 502)

    from app.services.cleanup_service import safe_remove_path
    if error:
        answer, charts = str(error), []
        # Only this turn's explicitly resolved output is discarded on failure.
        if output_directory is not None:
            safe_remove_path(str(output_directory))
    assistant = {
        "role": "assistant", "content": answer, "turn_id": turn_id,
        "charts": charts, "status": "error" if error else "ok",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        assistant["error_status"] = error.status_code
    updated = finish_exploration_turn(job_id, turn_id, assistant)
    if not updated:
        if output_directory is not None:
            safe_remove_path(str(output_directory))
        raise ExplorationError("A sessão foi removida durante a consulta.", 404)
    response = {"answer": answer, "charts": charts, "turn_id": turn_id, "history": updated["explore_history"]}
    if error:
        error.details.update(response)
        raise error
    return response


def complete_exploration_job(job_id: str, turn_id: str, prompt: str) -> None:
    """Background entry point: the outcome is stored in the session's explorer history."""
    try:
        complete_exploration(job_id, turn_id, prompt)
    except ExplorationError:
        pass


def run_exploration(job_id: str, prompt: str) -> dict:
    """Ask and answer a question synchronously (used by tests and scripts)."""
    started = start_exploration(job_id, prompt)
    return complete_exploration(job_id, started["turn_id"], (prompt or "").strip())
