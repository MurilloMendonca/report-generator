"""Provider clients and call_llm: retries, timeouts, call budgets and provider fallback.

Other llm modules call `client.call_llm(...)` through this module so tests can
patch app.services.llm.client.call_llm in one place."""

import logging
import os
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx
from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI

from app.services.session_manager import record_llm_usage
from app.utils.model_config import DEFAULT_MODEL_BY_PROVIDER, DEFAULT_MODEL_NAME, DEFAULT_PROVIDER, MODEL_CATALOG, current_model_name
from app.services.llm.errors import LLMBudgetError, LLMContractError, LLMTimeoutError

load_dotenv()
logger = logging.getLogger(__name__)


LLM_OPERATION_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("LLM_OPERATION_CONTEXT", default=None)


@contextmanager
def llm_operation_context(
    *,
    job_id: str | None = None,
    operation: str | None = None,
    settings: dict | None = None,
    provider: str | None = None,
    model_name: str | None = None,
):
    parent = LLM_OPERATION_CONTEXT.get()
    current = dict(parent or {})
    merged_settings = dict(current.get("settings") or {})
    if settings:
        merged_settings.update(settings)

    context = {
        **current,
        "job_id": job_id or current.get("job_id"),
        "operation": operation or current.get("operation"),
        "provider": provider or current.get("provider"),
        "model_name": model_name or current.get("model_name"),
        "settings": merged_settings,
        "call_count": int(current.get("call_count", 0) or 0),
        "attempt_count": int(current.get("attempt_count", 0) or 0),
    }
    token = LLM_OPERATION_CONTEXT.set(context)
    try:
        yield context
    finally:
        if parent is not None:
            parent["call_count"] = context["call_count"]
            parent["attempt_count"] = context["attempt_count"]
        LLM_OPERATION_CONTEXT.reset(token)


def current_llm_context() -> dict[str, Any]:
    return LLM_OPERATION_CONTEXT.get() or {}


def _llm_timeout_seconds(context: dict[str, Any], timeout_seconds: int | None = None) -> int:
    if timeout_seconds is not None:
        return max(1, int(timeout_seconds))
    settings = context.get("settings") or {}
    setting_timeout = settings.get("llm_timeout_seconds")
    if setting_timeout:
        return max(1, int(setting_timeout))
    env_timeout = os.getenv("PDF_REPORT_LLM_TIMEOUT_SECONDS")
    if env_timeout:
        return max(1, int(env_timeout))
    return 45


def _llm_call_budget(context: dict[str, Any]) -> int:
    settings = context.get("settings") or {}
    setting_budget = settings.get("llm_call_budget")
    if setting_budget is not None:
        return max(0, int(setting_budget))
    env_budget = os.getenv("PDF_REPORT_LLM_CALL_BUDGET")
    if env_budget:
        return max(0, int(env_budget))
    return 0


def _llm_fallback(context: dict[str, Any]) -> tuple[str | None, str | None]:
    settings = context.get("settings") or {}
    fallback_provider = settings.get("llm_fallback_provider") or os.getenv("PDF_REPORT_LLM_FALLBACK_PROVIDER")
    fallback_model = settings.get("llm_fallback_model_name") or os.getenv("PDF_REPORT_LLM_FALLBACK_MODEL_NAME")
    if fallback_provider:
        return str(fallback_provider), str(fallback_model) if fallback_model else None
    return None, None


@contextmanager
def _alarm_timeout(seconds: int):
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _handler(signum, frame):  # pragma: no cover - platform specific
        raise TimeoutError(f"LLM request timed out after {seconds}s")

    previous_handler = signal.signal(signal.SIGALRM, _handler)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] or previous_timer[1]:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def model_catalog() -> dict:
    return {
        "default_provider": DEFAULT_PROVIDER,
        "default_model": DEFAULT_MODEL_NAME,
        "defaults": DEFAULT_MODEL_BY_PROVIDER,
        "providers": MODEL_CATALOG,
    }


def normalize_gemini_model_name(model_name: str) -> str:
    return str(model_name or "").removeprefix("models/")


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment.")
    try:
        from google import genai
    except ImportError as exc:
        raise ImportError("google-genai is required for Gemini. Install dependencies with `pip install -r requirements.txt`.") from exc
    return genai.Client(api_key=api_key)


def get_deepseek_client():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not found in environment.")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com", max_retries=0)


def resolve_llm_config(provider: str = None, model_name: str = None) -> tuple[str, str]:
    provider = provider or DEFAULT_PROVIDER
    if provider not in DEFAULT_MODEL_BY_PROVIDER:
        raise ValueError(f"Unknown LLM provider '{provider}'.")
    model_name = model_name or DEFAULT_MODEL_BY_PROVIDER.get(provider)
    if not model_name:
        raise ValueError(f"No default model configured for provider '{provider}'.")
    normalized_model = current_model_name(provider, model_name)
    allowed_models = {model["id"] for model in MODEL_CATALOG.get(provider, [])}
    if allowed_models and normalized_model not in allowed_models:
        raise ValueError(f"Unknown model '{model_name}' for provider '{provider}'.")
    model_name = normalized_model
    return provider, model_name


def _request_llm_content(provider, model_name, system_instruction, user_prompt, timeout_seconds, pdf_bytes=None):
    client = None
    operation = current_llm_context().get("operation")
    object_output = operation in {
        "document_extraction", "document_extraction_pdf", "data_preprocessing",
        "text_generation", "text_condensation", "workspace_translation",
    }
    try:
        if provider == "gemini":
            client = get_gemini_client()
            contents = user_prompt
            if pdf_bytes is not None:
                from google.genai import types
                contents = [user_prompt, types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")]
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config={
                    "system_instruction": system_instruction,
                    **({"response_mime_type": "application/json"} if (object_output or operation == "artifact_plan") and not model_name.startswith("gemma-") else {}),
                    "http_options": {
                        "timeout": timeout_seconds * 1000,
                        "retry_options": {"attempts": 1},
                    },
                },
            )
            text = response.text
        else:
            client = get_deepseek_client()
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
                timeout=timeout_seconds,
                **({"response_format": {"type": "json_object"}} if object_output else {}),
            )
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise LLMContractError("llm", "Model output was truncated; reduce the requested output size")
            text = choice.message.content
        if not isinstance(text, str) or not text.strip():
            raise LLMContractError("llm", "Model returned an empty response")
        return text
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _retryable_llm_error(exc):
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return (
        isinstance(exc, (LLMTimeoutError, TimeoutError, httpx.TimeoutException, httpx.TransportError, APIConnectionError))
        or status in {408, 429, 500, 502, 503, 504}
    )


def call_llm(provider: str, model_name: str, system_instruction: str, user_prompt: str, retries=3, delay=10, timeout_seconds: int | None = None, allow_fallback: bool = True, *, _pdf_bytes: bytes | None = None):
    provider, model_name = resolve_llm_config(provider, model_name)
    context = current_llm_context()
    operation = context.get("operation") or "llm"
    job_id = context.get("job_id")
    timeout_seconds = _llm_timeout_seconds(context, timeout_seconds)
    budget = _llm_call_budget(context)
    prompt_chars = len(str(system_instruction or "")) + len(str(user_prompt or ""))

    last_error: Exception | None = None
    for i in range(retries):
        if budget and int(context.get("call_count", 0) or 0) >= budget:
            raise LLMBudgetError(operation, budget)
        attempt_number = i + 1
        start_time = time.perf_counter()
        try:
            with _alarm_timeout(timeout_seconds):
                text = _request_llm_content(
                    provider, model_name, system_instruction, user_prompt, timeout_seconds, _pdf_bytes,
                )

            duration_ms = (time.perf_counter() - start_time) * 1000
            if job_id:
                record_llm_usage(
                    job_id,
                    operation,
                    provider,
                    model_name,
                    attempt=attempt_number,
                    duration_ms=duration_ms,
                    status="ok",
                    prompt_chars=prompt_chars,
                    response_chars=len(text or ""),
                )
            context["call_count"] = int(context.get("call_count", 0) or 0) + 1
            context["attempt_count"] = int(context.get("attempt_count", 0) or 0) + 1
            return text
        except (TimeoutError, APITimeoutError, httpx.TimeoutException) as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            last_error = LLMTimeoutError(operation, timeout_seconds)
            if job_id:
                record_llm_usage(
                    job_id,
                    operation,
                    provider,
                    model_name,
                    attempt=attempt_number,
                    duration_ms=duration_ms,
                    status="timeout",
                    prompt_chars=prompt_chars,
                    response_chars=0,
                    error=str(last_error),
                )
            context["call_count"] = int(context.get("call_count", 0) or 0) + 1
            context["attempt_count"] = int(context.get("attempt_count", 0) or 0) + 1
            if i < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            break
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            last_error = e
            if job_id:
                record_llm_usage(
                    job_id,
                    operation,
                    provider,
                    model_name,
                    attempt=attempt_number,
                    duration_ms=duration_ms,
                    status="error",
                    prompt_chars=prompt_chars,
                    response_chars=0,
                    error=str(e)[:500],
                )
            context["call_count"] = int(context.get("call_count", 0) or 0) + 1
            context["attempt_count"] = int(context.get("attempt_count", 0) or 0) + 1
            if _retryable_llm_error(e):
                if i < retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
            break

    fallback_provider, fallback_model = _llm_fallback(context)
    if allow_fallback and fallback_provider and resolve_llm_config(fallback_provider, fallback_model) != (provider, model_name):
        logger.warning(
            "llm_fallback job_id=%s operation=%s provider=%s fallback_provider=%s",
            job_id or "",
            operation,
            provider,
            fallback_provider,
        )
        return call_llm(
            fallback_provider,
            fallback_model or DEFAULT_MODEL_BY_PROVIDER.get(fallback_provider),
            system_instruction,
            user_prompt,
            retries=max(1, retries - 1),
            delay=delay,
            timeout_seconds=timeout_seconds,
            allow_fallback=False,
        )

    if last_error:
        raise last_error
    raise RuntimeError("LLM call failed without raising an explicit error.")


def _document_pdf_provider(provider: str = None, model_name: str = None, settings: dict = None) -> tuple[str, str]:
    settings = settings or {}
    requested_provider = provider or DEFAULT_PROVIDER
    if requested_provider == "gemini":
        resolved = resolve_llm_config(provider, model_name)
        if not resolved[1].startswith("gemma-"):
            return resolved

    document_provider = settings.get("document_llm_provider") or os.getenv("PDF_REPORT_DOCUMENT_LLM_PROVIDER") or "gemini"
    document_model = settings.get("document_llm_model_name") or os.getenv("PDF_REPORT_DOCUMENT_LLM_MODEL_NAME")
    return resolve_llm_config(document_provider, document_model or DEFAULT_MODEL_BY_PROVIDER.get(document_provider))


def call_llm_with_pdf(
    provider: str,
    model_name: str,
    system_instruction: str,
    user_prompt: str,
    pdf_path: str,
    retries=3,
    delay=10,
    timeout_seconds: int | None = None,
    settings: dict = None,
):
    provider, model_name = _document_pdf_provider(provider, model_name, settings)
    if provider != "gemini":
        raise ValueError(f"Provider '{provider}' does not support PDF document extraction.")

    with open(pdf_path, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()
    return call_llm(
        provider, model_name, system_instruction, user_prompt,
        retries=retries, delay=delay, timeout_seconds=timeout_seconds,
        allow_fallback=False, _pdf_bytes=pdf_bytes,
    )
