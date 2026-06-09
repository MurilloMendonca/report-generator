import os
import hashlib
import json
import logging
import re
import signal
import threading
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from app.utils.layout import normalize_template
from app.utils.template_library import build_layout_from_template
from app.utils.component_library import (
    get_chart_style,
    get_theme,
    presets_prompt_summary,
    resolved_visual_context,
)
from app.utils.component_renderers import (
    hybrid_component_code,
    library_component_code,
    rename_generated_component_code,
    runtime_api_prompt_reference,
    supports_library_renderer,
)
from app.services.session_manager import record_llm_usage

load_dotenv()
logger = logging.getLogger(__name__)
LLM_OPERATION_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("LLM_OPERATION_CONTEXT", default=None)

PROMPTS_DIR = "prompts"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL_BY_PROVIDER = {
    "deepseek": "deepseek-v4-flash",
    "gemini": "gemini-3.1-flash-lite-preview",
}
DEFAULT_MODEL_NAME = DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER]


MODEL_CATALOG = {
    "gemini": [
        {"id": "gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash Lite Preview"},
        {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash Lite"},
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview"},
        {"id": "gemini-3-pro-preview", "label": "Gemini 3 Pro Preview"},
        {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview"},
        {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash"},
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite"},
        {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
        {"id": "gemini-2.0-flash-001", "label": "Gemini 2.0 Flash 001"},
        {"id": "gemini-2.0-flash-lite", "label": "Gemini 2.0 Flash Lite"},
        {"id": "gemini-2.0-flash-lite-001", "label": "Gemini 2.0 Flash Lite 001"},
        {"id": "gemini-pro-latest", "label": "Gemini Pro Latest"},
        {"id": "gemini-flash-latest", "label": "Gemini Flash Latest"},
        {"id": "gemini-flash-lite-latest", "label": "Gemini Flash Lite Latest"},
        {"id": "gemma-4-31b-it", "label": "Gemma 4 31B IT"},
        {"id": "gemma-4-26b-a4b-it", "label": "Gemma 4 26B A4B IT"},
    ],
    "deepseek": [
        {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    ],
}


class LlmArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    indicator: str = ""
    type: str = "graphic"
    reason: str = ""
    columns: list[str] = Field(default_factory=list)

    @field_validator("columns", mode="before")
    @classmethod
    def normalize_columns(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def ensure_required_fields(self):
        if not str(self.id or "").strip():
            raise ValueError("artifact id is required")
        if not str(self.indicator or "").strip():
            raise ValueError("artifact indicator is required")
        return self


class LlmPreprocessingPlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    needs_preprocessing: bool = False
    needs_filter: bool = False
    description: str = ""
    code: str = ""

    @model_validator(mode="after")
    def normalize_flags(self):
        if self.needs_filter:
            self.needs_preprocessing = True
        if self.needs_preprocessing:
            self.needs_filter = True
        if self.needs_preprocessing and not str(self.code or "").strip():
            raise ValueError("preprocessing code is required when preprocessing is enabled")
        return self


class LlmTranslationPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifacts: list[dict] = Field(default_factory=list)
    components: dict[str, str] = Field(default_factory=dict)


class LlmDocumentMetric(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    label: str = ""
    value: Any = None
    unit: str = ""
    period: str = ""
    source: str = ""
    confidence: str = "low"
    evidence: str = ""

    @model_validator(mode="after")
    def ensure_supported_metric(self):
        if not str(self.id or "").strip():
            raise ValueError("metric id is required")
        if not str(self.label or "").strip():
            raise ValueError("metric label is required")
        if self.value in (None, ""):
            raise ValueError("metric value is required")
        if not str(self.source or "").strip():
            raise ValueError("metric source is required")
        if not str(self.evidence or "").strip():
            raise ValueError("metric evidence is required")
        confidence = str(self.confidence or "low").lower()
        self.confidence = confidence if confidence in {"high", "medium", "low"} else "low"
        return self


class LlmDocumentInsights(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=list)
    metrics: list[LlmDocumentMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LLMContractError(ValueError):
    def __init__(
        self,
        stage: str,
        detail: str,
        *,
        field: str | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        raw_excerpt: str | None = None,
        cause: Exception | None = None,
    ):
        self.stage = stage
        self.field = field
        self.provider = provider
        self.model_name = model_name
        self.raw_excerpt = raw_excerpt
        self.cause = cause
        message = detail
        if field:
            message = f"{message} [field={field}]"
        if provider or model_name:
            message = f"{message} [provider={provider or 'unknown'} model={model_name or 'unknown'}]"
        super().__init__(message)

    def as_error_dict(self) -> dict[str, Any]:
        error = {
            "code": "llm_contract_error",
            "message": str(self),
            "stage": self.stage,
            "retryable": True,
        }
        if self.field:
            error["field"] = self.field
        if self.provider:
            error["provider"] = self.provider
        if self.model_name:
            error["model_name"] = self.model_name
        if self.raw_excerpt:
            error["raw_excerpt"] = self.raw_excerpt
        if self.cause:
            error["details"] = str(self.cause)
        return error


class LLMBudgetError(RuntimeError):
    def __init__(self, operation: str, budget: int):
        self.operation = operation
        self.budget = budget
        super().__init__(f"LLM call budget exceeded for {operation} ({budget})")

    def as_error_dict(self) -> dict[str, Any]:
        return {
            "code": "llm_budget_exceeded",
            "message": str(self),
            "stage": self.operation,
            "retryable": False,
            "budget": self.budget,
        }


class LLMTimeoutError(RuntimeError):
    def __init__(self, operation: str, timeout_seconds: int):
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(f"LLM call timed out after {timeout_seconds}s for {operation}")

    def as_error_dict(self) -> dict[str, Any]:
        return {
            "code": "llm_timeout",
            "message": str(self),
            "stage": self.operation,
            "retryable": True,
            "timeout_seconds": self.timeout_seconds,
        }


@contextmanager
def llm_operation_context(
    *,
    job_id: str | None = None,
    operation: str | None = None,
    settings: dict | None = None,
    provider: str | None = None,
    model_name: str | None = None,
):
    current = dict(LLM_OPERATION_CONTEXT.get() or {})
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


def _raw_excerpt(text: str, limit: int = 280) -> str:
    clean = strip_code_fence(text or "").strip()
    clean = " ".join(clean.split())
    return clean[:limit]


def _parse_contract_json(
    text: str,
    expected_type: type,
    stage: str,
    field: str,
    provider: str | None = None,
    model_name: str | None = None,
) -> Any:
    payload_text = extract_json_payload(text, "{" if expected_type is dict else "[", "}" if expected_type is dict else "]")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise LLMContractError(
            stage,
            f"Invalid JSON payload for {field}: {exc.msg}",
            field=field,
            provider=provider,
            model_name=model_name,
            raw_excerpt=_raw_excerpt(text),
            cause=exc,
        ) from exc

    if not isinstance(payload, expected_type):
        raise LLMContractError(
            stage,
            f"Expected {expected_type.__name__} payload for {field}, got {type(payload).__name__}",
            field=field,
            provider=provider,
            model_name=model_name,
            raw_excerpt=_raw_excerpt(text),
        )
    return payload


def parse_llm_artifacts(text: str, provider: str | None = None, model_name: str | None = None) -> list[dict]:
    payload = _parse_contract_json(text, list, "artifact_plan", "artifacts", provider, model_name)
    try:
        return [LlmArtifact.model_validate(item).model_dump(exclude_none=True) for item in payload]
    except ValidationError as exc:
        raise LLMContractError(
            "artifact_plan",
            "Artifact payload failed schema validation",
            field="artifacts",
            provider=provider,
            model_name=model_name,
            raw_excerpt=_raw_excerpt(text),
            cause=exc,
        ) from exc


def parse_llm_preprocessing_plan(text: str, provider: str | None = None, model_name: str | None = None) -> dict:
    payload = _parse_contract_json(text, dict, "data_preprocessing", "preprocessing_plan", provider, model_name)
    try:
        return LlmPreprocessingPlan.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise LLMContractError(
            "data_preprocessing",
            "Preprocessing payload failed schema validation",
            field="preprocessing_plan",
            provider=provider,
            model_name=model_name,
            raw_excerpt=_raw_excerpt(text),
            cause=exc,
        ) from exc


def parse_llm_text_map(text: str, provider: str | None = None, model_name: str | None = None) -> dict[str, str]:
    payload = _parse_contract_json(text, dict, "text_generation", "text_map", provider, model_name)
    return {
        str(key): str(value).strip()
        for key, value in payload.items()
        if isinstance(value, (str, int, float)) and str(value).strip()
    }


def parse_llm_translation_payload(text: str, provider: str | None = None, model_name: str | None = None) -> dict:
    payload = _parse_contract_json(text, dict, "workspace_translation", "translation_payload", provider, model_name)
    try:
        return LlmTranslationPayload.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise LLMContractError(
            "workspace_translation",
            "Translation payload failed schema validation",
            field="translation_payload",
            provider=provider,
            model_name=model_name,
            raw_excerpt=_raw_excerpt(text),
            cause=exc,
        ) from exc


def parse_llm_document_insights(text: str, provider: str | None = None, model_name: str | None = None) -> dict:
    payload = _parse_contract_json(text, dict, "document_extraction", "document_insights", provider, model_name)
    try:
        return LlmDocumentInsights.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise LLMContractError(
            "document_extraction",
            "Document insights payload failed schema validation",
            field="document_insights",
            provider=provider,
            model_name=model_name,
            raw_excerpt=_raw_excerpt(text),
            cause=exc,
        ) from exc


def parse_llm_refinement_target(text: str) -> str:
    target = strip_code_fence(text).strip()
    target = target.split()[0] if target else ""
    normalized_target = target.upper()
    allowed_targets = {"ALL", "LAYOUT", "SETTINGS"}
    if normalized_target in allowed_targets:
        return normalized_target
    if target and re.fullmatch(r"[A-Za-z0-9_\-]+", target):
        return target
    raise LLMContractError(
        "refinement_routing",
        f"Invalid refinement target '{target or ''}'",
        field="target",
        raw_excerpt=_raw_excerpt(text),
    )


def model_catalog() -> dict:
    return {
        "default_provider": DEFAULT_PROVIDER,
        "default_model": DEFAULT_MODEL_NAME,
        "providers": MODEL_CATALOG,
    }


def strip_code_fence(text: str, language: str = None) -> str:
    text = text.strip()
    fence = f"```{language}" if language else "```"
    if language and fence in text:
        return text.split(fence, 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def extract_json_payload(text: str, opening: str, closing: str) -> str:
    text = strip_code_fence(text, "json")
    if opening in text and closing in text:
        return text[text.find(opening):text.rfind(closing) + 1]
    return text


def extract_json_objects(text: str) -> list:
    decoder = json.JSONDecoder()
    objects = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
            if isinstance(value, dict):
                objects.append(value)
            index = start + max(end, 1)
        except json.JSONDecodeError:
            index = start + 1
    return objects


def normalize_metric_value(value):
    if value is None:
        return "N/A"
    if isinstance(value, float) and value != value:
        return "N/A"
    if isinstance(value, dict):
        cleaned = {}
        for key, raw_value in value.items():
            normalized_value = normalize_metric_value(raw_value)
            if normalized_value != "N/A":
                cleaned[str(key)] = normalized_value
        return cleaned or "N/A"
    if isinstance(value, list):
        cleaned = [normalize_metric_value(v) for v in value]
        cleaned = [v for v in cleaned if v != "N/A"]
        return cleaned or "N/A"
    if isinstance(value, str):
        value = value.strip()
        return value if value and "Error" not in value else "N/A"
    return value

def load_prompt(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file {filename} not found.")
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def prompt_fingerprint(filename: str) -> dict:
    content = load_prompt(filename)
    return {
        "filename": filename,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "version": content.splitlines()[0].strip("# ").strip() if content.splitlines() else filename,
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
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

def resolve_llm_config(provider: str = None, model_name: str = None) -> tuple[str, str]:
    provider = provider or DEFAULT_PROVIDER
    if provider not in DEFAULT_MODEL_BY_PROVIDER:
        raise ValueError(f"Unknown LLM provider '{provider}'.")
    model_name = model_name or DEFAULT_MODEL_BY_PROVIDER.get(provider)
    if not model_name:
        raise ValueError(f"No default model configured for provider '{provider}'.")
    normalized_model = normalize_gemini_model_name(model_name) if provider == "gemini" else str(model_name)
    allowed_models = {model["id"] for model in MODEL_CATALOG.get(provider, [])}
    if allowed_models and normalized_model not in allowed_models:
        raise ValueError(f"Unknown model '{model_name}' for provider '{provider}'.")
    model_name = normalized_model
    return provider, model_name


def call_llm(provider: str, model_name: str, system_instruction: str, user_prompt: str, retries=3, delay=10, timeout_seconds: int | None = None, allow_fallback: bool = True):
    provider, model_name = resolve_llm_config(provider, model_name)
    context = current_llm_context()
    operation = context.get("operation") or "llm"
    job_id = context.get("job_id")
    timeout_seconds = _llm_timeout_seconds(context, timeout_seconds)
    budget = _llm_call_budget(context)
    call_count = int(context.get("call_count", 0) or 0)
    attempt_count = int(context.get("attempt_count", 0) or 0)
    prompt_chars = len(str(system_instruction or "")) + len(str(user_prompt or ""))

    if budget and call_count >= budget:
        raise LLMBudgetError(operation, budget)

    last_error: Exception | None = None
    for i in range(retries):
        attempt_number = i + 1
        start_time = time.perf_counter()
        try:
            with _alarm_timeout(timeout_seconds):
                if provider == "gemini":
                    client = get_gemini_client()
                    response = client.models.generate_content(
                        model=normalize_gemini_model_name(model_name),
                        contents=f"{system_instruction}\n\n{user_prompt}",
                    )
                    text = response.text
                elif provider == "deepseek":
                    client = get_deepseek_client()
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": user_prompt},
                        ],
                        stream=False,
                        timeout=timeout_seconds,
                    )
                    text = response.choices[0].message.content
                else:
                    raise ValueError(f"Unsupported LLM provider '{provider}'.")

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
        except TimeoutError as exc:
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
            if "429" in str(e) or (hasattr(e, 'status_code') and e.status_code == 429):
                if i < retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
            break

    fallback_provider, fallback_model = _llm_fallback(context)
    if allow_fallback and fallback_provider and fallback_provider != provider:
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
        return resolve_llm_config(provider, model_name)

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

    context = current_llm_context()
    operation = context.get("operation") or "document_extraction"
    job_id = context.get("job_id")
    timeout_seconds = _llm_timeout_seconds(context, timeout_seconds)
    budget = _llm_call_budget(context)
    call_count = int(context.get("call_count", 0) or 0)
    prompt_chars = len(str(system_instruction or "")) + len(str(user_prompt or ""))

    if budget and call_count >= budget:
        raise LLMBudgetError(operation, budget)

    with open(pdf_path, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    last_error: Exception | None = None
    for i in range(retries):
        attempt_number = i + 1
        start_time = time.perf_counter()
        try:
            with _alarm_timeout(timeout_seconds):
                from google.genai import types

                client = get_gemini_client()
                response = client.models.generate_content(
                    model=normalize_gemini_model_name(model_name),
                    contents=[
                        f"{system_instruction}\n\n{user_prompt}",
                        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    ],
                )
                text = response.text

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
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            last_error = exc
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
                    error=str(exc)[:500],
                )
            context["call_count"] = int(context.get("call_count", 0) or 0) + 1
            context["attempt_count"] = int(context.get("attempt_count", 0) or 0) + 1
            if i < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            break

    if last_error:
        raise last_error
    raise RuntimeError("PDF LLM call failed without raising an explicit error.")

def generate_artifacts(
    all_metadata: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    template_context: dict = None,
    job_id: str | None = None,
) -> list:
    with llm_operation_context(job_id=job_id, operation="artifact_plan", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_ARTIFACTS.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        layout_constraints = ""
        if template_context:
            layout_constraints = (
                "Selected Layout Template:\n"
                f"{json.dumps(template_context, indent=2)}\n\n"
                "Hard constraint: generate visible text and graphic artifacts that fit this exact template. "
                "Metric artifacts can exceed the visual slot counts because they are not directly rendered.\n\n"
            )
        visual_catalog = json.dumps(presets_prompt_summary(), indent=2, ensure_ascii=False)
        user_prompt = (
            f"Target Language: {lang}\n"
            f"{layout_constraints}"
            f"Available Visual Component Presets:\n{visual_catalog}\n\n"
            f"Data Artifacts:\n{all_metadata}\n\n"
            f"User's Initial Prompt: {prompt}"
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                artifacts = parse_llm_artifacts(call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
                if isinstance(artifacts, list) and len(artifacts) > 0:
                    return artifacts
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "artifact_plan",
                "Failed to generate a valid artifact plan after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return []


def generate_document_insights(
    pdf_metadata: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    job_id: str | None = None,
) -> dict:
    with llm_operation_context(job_id=job_id, operation="document_extraction", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("EXTRACT_DOCUMENT_INSIGHTS.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        user_prompt = (
            f"Target Language: {lang}\n\n"
            f"User's Report Goal:\n{prompt}\n\n"
            f"PDF Metadata and Extracted Text:\n{pdf_metadata}"
        )

        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                return parse_llm_document_insights(call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "document_extraction",
                "Failed to extract valid document insights after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return {}


def generate_document_insights_from_pdf_file(
    pdf_path: str,
    pdf_metadata: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    job_id: str | None = None,
) -> dict:
    with llm_operation_context(job_id=job_id, operation="document_extraction_pdf", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("EXTRACT_DOCUMENT_INSIGHTS.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        user_prompt = (
            f"Target Language: {lang}\n\n"
            f"User's Report Goal:\n{prompt}\n\n"
            "Read the attached PDF directly. Use the metadata below only for source identification and extraction diagnostics; "
            "do not infer metric values from filenames.\n\n"
            f"PDF Metadata:\n{pdf_metadata}"
        )

        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                text = call_llm_with_pdf(
                    provider,
                    model_name,
                    system_instruction,
                    user_prompt,
                    pdf_path,
                    settings=settings,
                )
                payload = parse_llm_document_insights(text, provider, model_name)
                payload["extraction_source"] = "pdf_file"
                return payload
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "document_extraction",
                "Failed to extract valid PDF document insights after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return {}

def generate_template(artifacts: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, template_id: str = None) -> list:
    return normalize_template(build_layout_from_template(artifacts, template_id))


def normalize_preprocessing_payload(payload: dict) -> dict:
    needs_preprocessing = bool(payload.get("needs_preprocessing") or payload.get("needs_filter"))
    if not needs_preprocessing:
        return {
            "needs_preprocessing": False,
            "needs_filter": False,
            "description": "",
            "code": "",
        }

    code = strip_code_fence(str(payload.get("code", "")), "python").strip()
    if "def preprocess_dataframes" not in code and "def filter_dataframes" not in code:
        return {
            "needs_preprocessing": False,
            "needs_filter": False,
            "description": "",
            "code": "",
        }

    return {
        "needs_preprocessing": True,
        "needs_filter": True,
        "description": str(payload.get("description", "")).strip(),
        "code": code,
    }


def generate_data_filter(
    all_metadata: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    job_id: str | None = None,
) -> dict:
    with llm_operation_context(job_id=job_id, operation="data_preprocessing", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_DATA_FILTER.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        user_prompt = (
            f"Target Language: {lang}\n"
            f"User Goal:\n{prompt}\n\n"
            f"Raw Data Artifacts:\n{all_metadata}"
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                payload = parse_llm_preprocessing_plan(call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
                return normalize_preprocessing_payload(payload)
            except Exception as e:
                last_error = e
                time.sleep(2)

        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "data_preprocessing",
                "Failed to generate a valid preprocessing plan after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return {"needs_preprocessing": False, "needs_filter": False, "description": "", "code": ""}


def dataframe_load_line(original_name: str, path: str, indent: str = "") -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        return f"{indent}dataframes[{json.dumps(original_name)}] = pd.read_csv(r'{path}')"
    if ext in ['.xls', '.xlsx']:
        return f"{indent}dataframes[{json.dumps(original_name)}] = pd.read_excel(r'{path}')"
    return ""

def calculate_metric_value(
    all_metadata: str,
    artifact: dict,
    files_map: dict,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    job_id: str | None = None,
) -> any:
    """
    Generates and executes a script to calculate a metric value with automatic retries.
    """
    with llm_operation_context(job_id=job_id, operation="metric_value", provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_METRIC_VALUE.md")
        user_prompt = f"Target Metric:\n{json.dumps(artifact, indent=2)}\n\nData Artifacts:\n{all_metadata}"

        # Injetar carregamento dos dataframes para execução (com paths absolutos e raw strings)
        setup_code = ["import pandas as pd", "import json", "import os", "dataframes = {}"]
        for original_name, path in files_map.items():
            abs_path = os.path.abspath(path)
            line = dataframe_load_line(original_name, abs_path)
            if line:
                setup_code.append(line)

        for attempt in range(3):
            try:
                code = call_llm(provider, model_name, system_instruction, f"{user_prompt}\n\nAttempt: {attempt+1}").strip()
                code = strip_code_fence(code, "python")

                full_script = "\n".join(setup_code) + "\n\n" + code

                from app.services.executor_service import execute_script
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
                    result = execute_script(full_script, tmp.name)
                    os.unlink(tmp.name) # Cleanup manual

                    if result["success"]:
                        output = result["stdout"].strip()
                        for payload in reversed(extract_json_objects(output)):
                            val = normalize_metric_value(payload.get("value"))
                            if val != "N/A":
                                return val

                    logger.warning(
                        "metric execution failed metric_id=%s attempt=%s stderr=%s",
                        artifact.get("id", ""),
                        attempt + 1,
                        (result.get("stderr") or "")[:500],
                    )
            except Exception as e:
                logger.exception("metric execution exception metric_id=%s attempt=%s", artifact.get("id", ""), attempt + 1)

            time.sleep(1)

        return "N/A"

VISUAL_COMPONENT_TYPES = {'graphic', 'bar', 'line', 'pie', 'scatter', 'table', 'chart', 'radial', 'donut', 'gauge'}


def chart_code_has_layout_risk(code: str) -> bool:
    lowered = str(code or "").lower()
    has_x_label = bool(re.search(r"\.set_xlabel\(\s*['\"][^'\"]+['\"]", lowered))
    has_bottom_legend = bool(re.search(r"bbox_to_anchor\s*=\s*\([^)]*-\d*\.?\d+", lowered))
    if has_x_label and has_bottom_legend:
        return True

    has_data_annotation = any(token in lowered for token in [
        "ax.annotate",
        "plt.annotate",
        "ax.text(",
        "plt.text(",
    ])
    if not has_data_annotation:
        return False

    has_inside_upper_legend = any(token in lowered for token in [
        "legend(loc='upper right'",
        'legend(loc="upper right"',
        "legend(loc='upper left'",
        'legend(loc="upper left"',
        "legend(loc='best'",
        'legend(loc="best"',
    ])
    if not has_inside_upper_legend:
        return False

    return "bbox_to_anchor" not in lowered


def slot_by_artifact_id(template: list) -> dict:
    return {item.get("id"): item for item in normalize_template(template or []) if item.get("id")}


def visual_slot_guidance(block: dict | None) -> dict | None:
    if not block:
        return None
    width = block.get("w") or 0
    height = block.get("h") or 0
    compact = width < 110 or height < 75
    return {
        "page": block.get("page", 1),
        "x_mm": block.get("x"),
        "y_mm": block.get("y"),
        "w_mm": width,
        "h_mm": height,
        "compact": compact,
        "guidance": (
            "Compact chart block: prioritize a simple chart, small title, no non-essential data labels, "
            "and avoid bottom legend plus x-axis label."
            if compact else
            "Larger chart block: reserve explicit margins for title, tick labels, and legend."
        ),
    }


def text_layout_guidance(text_artifacts: list, template: list, settings: dict = None) -> list:
    blocks_by_id = slot_by_artifact_id(template)
    guidance = []
    for artifact in text_artifacts:
        artifact_id = artifact.get("id")
        block = blocks_by_id.get(artifact_id)
        if not block:
            continue
        capacity = estimate_text_block_capacity(block, settings)
        guidance.append({
            "id": artifact_id,
            "indicator": artifact.get("indicator", artifact_id),
            "slot": {
                "page": block.get("page", 1),
                "w_mm": block.get("w"),
                "h_mm": block.get("h"),
            },
            "target_words": capacity["target_words"],
            "soft_max_words": capacity["soft_max_words"],
            "max_lines": capacity["max_lines"],
        })
    return guidance


def generate_component_code(all_metadata: str, artifact: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, calculated_metrics: dict = None, preceding_text: str = None, settings: dict = None, layout_slot: dict = None, job_id: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="component_code", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_COMPONENT_CODE.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        context = [
            f"Target Language: {lang}",
            f"Target Component JSON:\n{json.dumps(artifact, indent=2)}",
            f"Data Artifacts:\n{all_metadata}"
        ]

        if calculated_metrics:
            context.append(f"Calculated Metrics (Available for reference):\n{json.dumps(calculated_metrics, indent=2)}")

        if preceding_text:
            context.append(f"Preceding Report Text (for context/conclusion):\n{preceding_text}")

        slot_guidance = visual_slot_guidance(layout_slot)
        if slot_guidance:
            context.append(f"Layout Slot Constraints:\n{json.dumps(slot_guidance, indent=2, ensure_ascii=False)}")

        if artifact.get("type") in VISUAL_COMPONENT_TYPES:
            visual_context = resolved_visual_context(artifact, settings or {})
            context.append(
                "Component Preset Contract:\n"
                f"{json.dumps(visual_context['preset'], indent=2, ensure_ascii=False)}"
            )
            context.append(
                "Resolved Visual Theme:\n"
                f"{json.dumps(visual_context['theme'], indent=2, ensure_ascii=False)}"
            )
            context.append(
                "Resolved Chart Style:\n"
                f"{json.dumps(visual_context['style'], indent=2, ensure_ascii=False)}"
            )
            context.append(
                "Resolved Visual Options:\n"
                f"{json.dumps(visual_context['options'], indent=2, ensure_ascii=False)}"
            )
            if artifact.get("data_binding"):
                context.append(
                    "Data Binding Contract:\n"
                    f"{json.dumps(artifact.get('data_binding'), indent=2, ensure_ascii=False)}"
                )
            if artifact.get("render_mode"):
                context.append(f"Render Mode: {artifact.get('render_mode')}")
            context.append(
                "Runtime Renderer API Reference:\n"
                f"{runtime_api_prompt_reference(artifact.get('component_preset_id'))}"
            )

        if artifact.get("type") in VISUAL_COMPONENT_TYPES and settings:
            visual_instructions = settings.get("visual_instructions")
            chart_palette = settings.get("chart_palette")
            if visual_instructions:
                context.append(f"Visual Style Instructions:\n{visual_instructions}")
            if chart_palette:
                context.append(
                    "Preferred Chart Palette:\n"
                    f"{json.dumps(chart_palette, indent=2, ensure_ascii=False)}\n"
                    "Use these colors for chart marks instead of default blue palettes."
                )

        user_prompt = "\n\n".join(context)

        last_code = ""
        for attempt in range(3):
            attempt_prompt = user_prompt
            if attempt > 0:
                attempt_prompt += (
                    "\n\nPrevious attempt violated chart layout safety: it combined data labels "
                    "or annotations with an inside upper-corner legend. Regenerate the function "
                    "with the legend outside the axes using bbox_to_anchor, or remove non-essential "
                    "data labels/annotations."
                )

            text = call_llm(provider, model_name, system_instruction, attempt_prompt)
            code = strip_code_fence(text, "python")
            last_code = code
            if artifact.get("type") not in VISUAL_COMPONENT_TYPES or not chart_code_has_layout_risk(code):
                return code
            time.sleep(1)

        return last_code

def generate_all_texts(all_metadata: str, text_artifacts: list, calculated_metrics: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None, template: list = None, job_id: str | None = None) -> dict:
    with llm_operation_context(job_id=job_id, operation="text_generation", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_ALL_TEXTS.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        layout_guidance = text_layout_guidance(text_artifacts, template or [], settings)
        context = [
            f"Target Language: {lang}",
            f"Text Artifacts:\n{json.dumps(text_artifacts, indent=2, ensure_ascii=False)}",
            f"Calculated Metrics:\n{json.dumps(calculated_metrics, indent=2, ensure_ascii=False)}",
        ]
        if layout_guidance:
            context.append(f"Layout Slots:\n{json.dumps(layout_guidance, indent=2, ensure_ascii=False)}")
        context.append(f"Data Artifacts:\n{all_metadata}")
        user_prompt = "\n\n".join(context)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return parse_llm_text_map(call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "text_generation",
                "Failed to generate valid narrative texts after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return {}


def estimate_text_block_capacity(block: dict, settings: dict = None) -> dict:
    settings = settings or {}
    font_size = settings.get("font_size", 10) or 10
    line_height = settings.get("line_height", 5) or 5
    title_reserve = 6 if (block.get("h") or 0) < 55 else 7
    body_height = max(1, (block.get("h") or 0) - title_reserve)
    chars_per_line = max(18, int((block.get("w") or 0) * 0.62 * (10 / font_size)))
    max_lines = max(1, int(body_height / max(line_height, 1)))
    raw_words = chars_per_line * max_lines / 6.2
    target_words = max(24, int(raw_words * 0.92))
    soft_max_words = max(target_words + 8, int(raw_words * 1.08))
    return {
        "chars_per_line": chars_per_line,
        "max_lines": max_lines,
        "max_chars": int(chars_per_line * max_lines * 1.02),
        "target_words": target_words,
        "soft_max_words": soft_max_words,
    }


def estimated_line_count(text: str, chars_per_line: int) -> int:
    total = 0
    for paragraph in str(text or "").splitlines() or [""]:
        total += max(1, len(paragraph.strip()) // max(chars_per_line, 1) + 1)
    return total


def condense_texts_to_fit(texts_map: dict, text_artifacts: list, template: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None, job_id: str | None = None) -> dict:
    with llm_operation_context(job_id=job_id, operation="text_condensation", settings=settings, provider=provider, model_name=model_name):
        texts_map = dict(texts_map or {})
        blocks_by_id = {item.get("id"): item for item in normalize_template(template)}
        overflow_items = []
        for artifact in text_artifacts:
            artifact_id = artifact.get("id")
            block = blocks_by_id.get(artifact_id)
            text = texts_map.get(artifact_id)
            if not block or not text:
                continue
            capacity = estimate_text_block_capacity(block, settings)
            lines = estimated_line_count(text, capacity["chars_per_line"])
            overflow_threshold = max(capacity["max_lines"] + 1, int(capacity["max_lines"] * 1.18))
            if lines > overflow_threshold:
                overflow_items.append({
                    "id": artifact_id,
                    "indicator": artifact.get("indicator", artifact_id),
                    "slot": {"w_mm": block.get("w"), "h_mm": block.get("h")},
                    "estimated_lines": lines,
                    "max_lines": capacity["max_lines"],
                    "overflow_threshold_lines": overflow_threshold,
                    "target_words": capacity["target_words"],
                    "soft_max_words": capacity["soft_max_words"],
                    "text": text,
                })

        if not overflow_items:
            return texts_map

        lang = settings.get("language", "Portuguese") if settings else "Portuguese"
        system_instruction = (
            "You are an executive editor for PDF report text blocks. Rewrite only the provided "
            "overflowing text components so they fit their exact PDF slots. Preserve the most "
            "important metrics, conclusions, and analytical meaning. Return only valid JSON."
        )
        user_prompt = (
            f"Target Language: {lang}\n"
            "Each item below overflowed its assigned PDF text block. Condense each text to no more "
            "than its soft_max_words, and only go down to target_words if strictly necessary. "
            "Use complete sentences, no markdown, no bullet lists, and no ellipses. "
            "Return a JSON object mapping each id to the condensed string.\n\n"
            f"Overflowing Text Blocks:\n{json.dumps(overflow_items, indent=2, ensure_ascii=False)}"
        )

        for attempt in range(3):
            try:
                response = call_llm(provider, model_name, system_instruction, user_prompt)
                payload = parse_llm_text_map(response)
                for key, value in payload.items():
                    if key in texts_map and isinstance(value, str) and value.strip():
                        texts_map[key] = value.strip()
                return texts_map
            except Exception as e:
                logger.warning("text condensation failed attempt=%s error=%s", attempt + 1, str(e))
                time.sleep(2)

        return texts_map


def generate_report_title(all_metadata: str, prompt: str, artifacts: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None, job_id: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="title_generation", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_REPORT_TITLE.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        user_prompt = (
            f"Target Language: {lang}\n"
            f"User Goal:\n{prompt}\n\n"
            f"Data Artifacts:\n{all_metadata}\n\n"
            f"Selected Artifacts:\n{json.dumps(artifacts, indent=2, ensure_ascii=False)}"
        )

        try:
            title = call_llm(provider, model_name, system_instruction, user_prompt).strip()
            title = strip_code_fence(title).strip().strip('"').strip("'")
            title = " ".join(title.split())
            if title:
                return title
        except Exception as e:
            logger.warning("title generation failed error=%s", str(e))

        return fallback_report_title(prompt)


def fallback_report_title(prompt: str) -> str:
    clean_prompt = " ".join((prompt or "").split())
    if not clean_prompt:
        return "Relatório Analítico"
    return clean_prompt

def assemble_script(
    components: dict,
    template: list,
    files_map: dict,
    output_pdf_path: str,
    settings: dict = None,
    page_format: tuple[float, float] | None = None,
    include_title: bool = True,
    normalize_layout: bool = True,
    artifacts: list[dict] | None = None,
) -> str:
    template = sorted(
        normalize_template(template) if normalize_layout else [item.copy() for item in template or []],
        key=lambda item: (item.get("page", 1), item.get("y", 0), item.get("x", 0))
    )
    # Default settings if none provided
    if not settings:
        settings = {
            "title": "Relatório Analítico",
            "font": "Arial",
            "font_size": 10,
            "alignment": "J",
            "line_height": 5
        }
    
    # fpdf2 uses standard names like 'helvetica', 'times', 'courier'
    font_family = settings.get('font', 'Arial').lower()
    if font_family == 'arial': font_family = 'helvetica'
    chart_theme = get_theme(settings.get("visual_theme_id"))
    chart_style = get_chart_style(settings.get("chart_style_id"))
    chart_palette = settings.get('chart_palette') or chart_theme.get("palette") or []
    if not isinstance(chart_palette, list):
        chart_palette = []
    
    artifacts_by_id = {
        str(artifact.get("id")): artifact
        for artifact in artifacts or []
        if isinstance(artifact, dict) and artifact.get("id")
    }

    script = [
        "import os", "os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')",
        "import json",
        "import pandas as pd", "import numpy as np", "import matplotlib as mpl", "import matplotlib.pyplot as plt", "import seaborn as sns",
        "from fpdf import FPDF, XPos, YPos", "import tempfile", "import textwrap", "",
        "CHART_TITLE_FONTSIZE = 9",
        "CHART_TITLE_FONTWEIGHT = 'bold'",
        "CHART_TITLE_PAD = 6",
        "CHART_LABEL_FONTSIZE = 7",
        "CHART_TICK_FONTSIZE = 6",
        "CHART_LEGEND_FONTSIZE = 6",
        "CHART_TABLE_FONTSIZE = 6",
        f"CHART_PALETTE = {json.dumps(chart_palette)}",
        f"CHART_THEME = {json.dumps(chart_theme, ensure_ascii=False)}",
        f"CHART_STYLE = {json.dumps(chart_style, ensure_ascii=False)}",
        "sns.set_theme(style=CHART_THEME.get('seaborn_style', 'whitegrid'))",
        "if CHART_PALETTE:",
        "    sns.set_palette(CHART_PALETTE)",
        "    mpl.rcParams['axes.prop_cycle'] = plt.cycler(color=CHART_PALETTE)",
        "mpl.rcParams.update({",
        "    'axes.titlesize': CHART_TITLE_FONTSIZE,",
        "    'axes.titleweight': CHART_TITLE_FONTWEIGHT,",
        "    'axes.labelsize': CHART_LABEL_FONTSIZE,",
        "    'xtick.labelsize': CHART_TICK_FONTSIZE,",
        "    'ytick.labelsize': CHART_TICK_FONTSIZE,",
        "    'legend.fontsize': CHART_LEGEND_FONTSIZE,",
        "    'figure.facecolor': CHART_THEME.get('figure_facecolor', '#ffffff'),",
        "    'axes.facecolor': CHART_THEME.get('axes_facecolor', '#ffffff'),",
        "    'axes.edgecolor': CHART_THEME.get('grid_color', '#e5e7eb'),",
        "    'axes.labelcolor': CHART_THEME.get('text_color', '#111827'),",
        "    'xtick.color': CHART_THEME.get('muted_text_color', '#6b7280'),",
        "    'ytick.color': CHART_THEME.get('muted_text_color', '#6b7280'),",
        "    'grid.color': CHART_THEME.get('grid_color', '#e5e7eb'),",
        "    'text.color': CHART_THEME.get('text_color', '#111827'),",
        "})",
        "",
        "import pdf_report_renderers.runtime as render_runtime",
        "from pdf_report_renderers.runtime import (",
        "    configure_visual_runtime, render_visual_component, render_bar_chart,",
        "    render_line_chart, render_scatter_plot, render_donut_chart, render_gauge_chart,",
        "    render_summary_table, render_detail_table, _resolved_chart_theme,",
        "    _chart_colors, _apply_chart_theme, _format_compact_number,",
        "    _top_n_with_others, _save_chart_to_pdf, _render_table_image,",
        "    _library_dataframe, _library_resolve_column, _library_resolve_columns,",
        "    _library_aggregate, _library_limit_top, _library_empty_chart,",
        ")",
        "configure_visual_runtime(CHART_THEME, CHART_PALETTE, CHART_STYLE)",
        "",
        "def _safe_pdf_text(value):",
        "    replacements = {",
        "        '\\u2013': '-', '\\u2014': '-', '\\u2015': '-',",
        "        '\\u2018': \"'\", '\\u2019': \"'\", '\\u201c': '\"', '\\u201d': '\"',",
        "        '\\u2022': '-', '\\u00a0': ' ', '\\u2026': '...'",
        "    }",
        "    text = str(value or '')",
        "    for source, target in replacements.items():",
        "        text = text.replace(source, target)",
        "    return text.encode('latin-1', 'replace').decode('latin-1')",
        "",
        "def _wrap_lines(text, max_chars):",
        "    lines = []",
        "    for paragraph in str(text).splitlines() or ['']:",
        "        wrapped = textwrap.wrap(paragraph.strip(), width=max_chars) or ['']",
        "        lines.extend(wrapped)",
        "    return lines",
        "",
        "def _sentence_chunks(text):",
        "    normalized = ' '.join(str(text).split())",
        "    chunks, current = [], []",
        "    for token in normalized.split(' '):",
        "        current.append(token)",
        "        if token.rstrip().endswith(('.', '!', '?', ';')):",
        "            chunks.append(' '.join(current))",
        "            current = []",
        "    if current:",
        "        chunks.append(' '.join(current))",
        "    return chunks or [normalized]",
        "",
        "def _fit_text_to_box(text, w, h, font_size, line_height):",
        "    max_chars = max(14, int(w * 0.55 * (10 / max(font_size, 1))))",
        "    max_lines = max(1, int(h / max(line_height, 1)))",
        "    if len(_wrap_lines(text, max_chars)) <= max_lines:",
        "        return text",
        "    selected = []",
        "    for sentence in _sentence_chunks(text):",
        "        candidate = ' '.join(selected + [sentence]).strip()",
        "        if not candidate:",
        "            continue",
        "        if len(_wrap_lines(candidate, max_chars)) <= max_lines:",
        "            selected.append(sentence)",
        "        elif selected:",
        "            break",
        "        else:",
        "            words = sentence.split()",
        "            compact = []",
        "            for word in words:",
        "                candidate = ' '.join(compact + [word])",
        "                if len(_wrap_lines(candidate, max_chars)) > max_lines:",
        "                    break",
        "                compact.append(word)",
        "            selected.append(' '.join(compact))",
        "            break",
        "    fitted = ' '.join(selected).strip()",
        "    return fitted",
        "",
        "def _write_report_title(pdf, title, font_family):",
        "    clean_title = ' '.join(_safe_pdf_text(title or 'Relatório Analítico').split())",
        "    for size in range(16, 10, -1):",
        "        max_chars = max(28, int(58 * (16 / size)))",
        "        lines = textwrap.wrap(clean_title, width=max_chars) or [clean_title]",
        "        if len(lines) <= 2:",
        "            pdf.set_font(font_family, 'B', size)",
        "            pdf.multi_cell(0, 8, '\\n'.join(lines), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')",
        "            return",
        "    pdf.set_font(font_family, 'B', 11)",
        "    pdf.multi_cell(0, 7, '\\n'.join(textwrap.wrap(clean_title, width=84)), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')",
        "",
        "def render_text_block(pdf, text, x, y, w, h, title=None):",
        f"    font_size = {settings.get('font_size', 10)}",
        f"    line_height = {settings.get('line_height', 5)}",
        "    text = _safe_pdf_text(text)",
        "    title = _safe_pdf_text(title) if title else None",
        "    body_height = h",
        f"    pdf.set_xy(x, y)",
        f"    if title:",
        f"        pdf.set_font('{font_family}', 'B', font_size)",
        f"        title_line_height = max(4.5, line_height)",
        f"        title_max_chars = max(12, int(w * 0.55 * (10 / max(font_size, 1))))",
        f"        title_lines = _wrap_lines(title, title_max_chars)",
        f"        available_lines = max(1, int(max(h - line_height, title_line_height) / title_line_height))",
        f"        title_lines = title_lines[:available_lines]",
        f"        pdf.multi_cell(w, title_line_height, '\\n'.join(title_lines), new_x=XPos.LMARGIN, new_y=YPos.NEXT)",
        f"        title_height = len(title_lines) * title_line_height + 1",
        f"        body_height = max(1, h - title_height)",
        f"        pdf.set_xy(x, pdf.get_y())",
        f"    ",
        f"    fitted_text = _fit_text_to_box(text, w, body_height, font_size, line_height)",
        f"    pdf.set_font('{font_family}', '', font_size)",
        f"    pdf.multi_cell(w, line_height, fitted_text, align='{settings.get('alignment', 'J')}')",
        ""
    ]
    library_wrapper_ids = set()
    for comp_id, func_code in components.items():
        artifact = artifacts_by_id.get(str(comp_id))
        render_mode = str((artifact or {}).get("render_mode") or "generated_code")
        if artifact and render_mode == "hybrid" and supports_library_renderer(artifact):
            script.append(rename_generated_component_code(str(comp_id), func_code))
        else:
            script.append(func_code)
            if artifact and render_mode == "library" and supports_library_renderer(artifact):
                library_wrapper_ids.add(str(comp_id))
        script.append("")

    for item in template:
        artifact = artifacts_by_id.get(str(item.get("id")))
        if not artifact or not supports_library_renderer(artifact):
            continue
        render_mode = str(artifact.get("render_mode") or "generated_code")
        if render_mode not in {"library", "hybrid"}:
            continue
        artifact_id = str(artifact.get("id"))
        if artifact_id in library_wrapper_ids:
            continue
        wrapper = hybrid_component_code(artifact) if render_mode == "hybrid" else library_component_code(artifact)
        script.append(wrapper)
        script.append("")
        library_wrapper_ids.add(artifact_id)

    script.append("def generate_report():")
    script.append("    dataframes = {}")
    script.append("    import logging")
    for original_name, path in files_map.items():
        line = dataframe_load_line(original_name, path, indent="    ")
        if line:
            script.append(line)

    if page_format:
        script.append(f"    pdf = FPDF(format=({float(page_format[0])}, {float(page_format[1])}))")
    else:
        script.append("    pdf = FPDF()")
    script.append("    pdf.set_auto_page_break(auto=False)")
    script.append("    current_page = 0")
    script.append("    component_manifest = []")
    script.append("    def ensure_page(page):")
    script.append("        nonlocal current_page")
    script.append("        while current_page < page:")
    script.append("            pdf.add_page()")
    script.append("            current_page += 1")
    if include_title:
        script.append("            if current_page == 1:")
        script.append(f"                _write_report_title(pdf, {json.dumps(settings.get('title') or 'Relatório Analítico')}, '{font_family}')")
        script.append("                pdf.ln(3)")
    else:
        script.append("            pass")
    script.append("    ensure_page(1)")
    for item in template:
        page = item.get("page", 1)
        script.append(f"    ensure_page({page})")
        script.append(f"    try:")
        script.append(f"        # Provide a default 'df' for backward compatibility with older prompts/scripts")
        script.append(f"        primary_df = list(dataframes.values())[0] if dataframes else None")
        script.append(f"        try:")
        script.append(f"            render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']}, df=primary_df)")
        script.append(f"        except TypeError:")
        script.append(f"            render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']})")
        script.append(f"        component_manifest.append({{'id': {json.dumps(item['id'])}, 'status': 'rendered'}})")
        script.append(f"    except Exception as e: ")
        script.append(f"        component_manifest.append({{'id': {json.dumps(item['id'])}, 'status': 'failed', 'error': str(e)}})")
        script.append(f"        logging.getLogger(__name__).exception('Erro no componente {json.dumps(item['id'])}')")
    script.append(f"    manifest_path = r'{output_pdf_path}.manifest.json'")
    script.append("    with open(manifest_path, 'w', encoding='utf-8') as manifest_file:")
    script.append("        json.dump({'components': component_manifest}, manifest_file, indent=2, ensure_ascii=False)")
    script.append("    failed_components = [item for item in component_manifest if item.get('status') != 'rendered']")
    script.append("    if failed_components:")
    script.append("        raise RuntimeError('Report component rendering failed: ' + ', '.join(item['id'] for item in failed_components))")
    script.append(f"    pdf.output(r'{output_pdf_path}')")
    script.append("")
    script.append("if __name__ == '__main__':")
    script.append("    generate_report()")
    return "\n".join(script)

def fix_script(current_script: str, error: str, all_metadata: str, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, job_id: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="script_fix", provider=provider, model_name=model_name):
        system_instruction = load_prompt("FIX_SCRIPT.md")
        user_prompt = f"Reported Error: {error}\n\nCurrent Failing Script:\n{current_script}\n\nData Artifacts:\n{all_metadata}"

        text = call_llm(provider, model_name, system_instruction, user_prompt)
        return strip_code_fence(text, "python")

def generate_exploration_script(all_metadata: str, query: str, files_map: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, history: list = None, job_id: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="exploration_script", provider=provider, model_name=model_name):
        system_instruction = load_prompt("EXPLORE_DATA.md")
        history_str = ""
        if history:
            history_str = "Conversation History:\n"
            for h in history: history_str += f"{h['role'].upper()}: {h['content']}\n"

        user_prompt = f"{history_str}\nData Artifacts:\n{all_metadata}\n\nUser Query: {query}"

        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = strip_code_fence(text, "python")

        # Injetar carregamento dos dataframes
        setup_code = [
            "import pandas as pd",
            "dataframes = {}"
        ]
        for original_name, path in files_map.items():
            line = dataframe_load_line(original_name, path)
            if line:
                setup_code.append(line)

        full_script = "\n".join(setup_code) + "\n\n" + text
        return full_script

def translate_workspace(session: dict, target_lang: str, job_id: str | None = None) -> dict:
    provider = session.get('provider', DEFAULT_PROVIDER)
    model_name = session.get('model_name')
    with llm_operation_context(job_id=job_id or session.get("job_id"), operation="workspace_translation", settings=session.get("settings"), provider=provider, model_name=model_name):
        system_instruction = load_prompt("TRANSLATE_WORKSPACE.md")

        workspace_data = {
            "target_language": target_lang,
            "artifacts": session.get('artifacts', []),
            "components": session.get('components', {})
        }

        user_prompt = f"Workspace to Translate:\n{json.dumps(workspace_data, indent=2)}"

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return parse_llm_translation_payload(call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "workspace_translation",
                "Failed to translate workspace after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return None


COLOR_REFINEMENT_VALUES = {
    "vermelho": "#dc2626",
    "red": "#dc2626",
    "azul": "#2563eb",
    "blue": "#2563eb",
    "verde": "#16a34a",
    "green": "#16a34a",
    "amarelo": "#f59e0b",
    "yellow": "#f59e0b",
    "roxo": "#7c3aed",
    "purple": "#7c3aed",
    "cinza": "#6b7280",
    "gray": "#6b7280",
    "grey": "#6b7280",
    "preto": "#111827",
    "black": "#111827",
}


def _normalize_refinement_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(normalized.lower().split())


def requested_visual_color(prompt: str) -> str | None:
    normalized = _normalize_refinement_text(prompt)
    if not any(term in normalized for term in {"cor", "color", "cores", "colors", "paleta", "palette"}):
        return None
    requested = None
    requested_position = -1
    for name, color in COLOR_REFINEMENT_VALUES.items():
        position = normalized.rfind(name)
        if position > requested_position:
            requested = color
            requested_position = position
    return requested


def refine_session_modular(session: dict, new_prompt: str, job_id: str | None = None) -> dict:
    provider = session.get('provider', DEFAULT_PROVIDER)
    model_name = session.get('model_name')
    settings = session.get('settings', {})
    lang = settings.get('language', 'Portuguese')
    base_job_id = job_id or session.get("job_id")

    with llm_operation_context(job_id=base_job_id, operation="refinement_routing", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("REFINE_MODULAR.md")

        artifacts_str = json.dumps([{"id": a["id"], "name": a["indicator"]} for a in session.get("artifacts", [])], indent=2)
        user_prompt = f"Target Language: {lang}\nUser Feedback/Prompt: \"{new_prompt}\"\n\nCurrent List of Indicators:\n{artifacts_str}"

        target = parse_llm_refinement_target(call_llm(provider, model_name, system_instruction, user_prompt))

        all_metadata = json.dumps([f['artifact'] for f in session['files'] if f.get('artifact')], indent=2)

        if target in [a["id"] for a in session.get("artifacts", [])]:
            artifact = next(a for a in session["artifacts"] if a["id"] == target)
            requested_color = requested_visual_color(new_prompt)
            if requested_color and artifact.get("type") in VISUAL_COMPONENT_TYPES and supports_library_renderer(artifact):
                visual_options = dict(artifact.get("visual_options") or {})
                visual_options["color"] = requested_color
                if artifact.get("component_preset_id") == "line_timeseries" or artifact.get("type") == "line":
                    visual_options["line_color"] = requested_color
                else:
                    visual_options["bar_color"] = requested_color
                artifact["visual_options"] = visual_options
                artifact["render_mode"] = "library"
                session.setdefault("components", {}).pop(target, None)
                session["needs_script_update"] = True
                return session
            current_code = session.get('components', {}).get(
                target,
                "No custom function exists yet; this component may currently be rendered by the runtime library."
            )
            runtime_reference = ""
            if artifact.get("type") in VISUAL_COMPONENT_TYPES:
                runtime_reference = (
                    "\n\nTarget Component JSON:\n"
                    f"{json.dumps(artifact, indent=2, ensure_ascii=False)}"
                    "\n\nRuntime Renderer API Reference:\n"
                    f"{runtime_api_prompt_reference(artifact.get('component_preset_id'))}"
                    "\n\nRefinement Rendering Rules:\n"
                    "- The returned function will be the primary renderer after this refinement.\n"
                    "- Prefer a short custom function that calls the runtime API or adapts a small dataframe before calling a helper.\n"
                    "- Only write bespoke matplotlib/table code for the part the user explicitly customized."
                )
            refine_instruction = (
                f"Target Language: {lang}\n"
                f"Update the FUNCTION render_{target} based on: {new_prompt}\n\n"
                f"Current Code:\n{current_code}\n\n"
                f"Data Artifacts:\n{all_metadata}"
                f"{runtime_reference}"
            )

            with llm_operation_context(job_id=base_job_id, operation="refinement_component", settings=settings, provider=provider, model_name=model_name):
                new_func = call_llm(provider, model_name, "Expert Python Developer specialized in report components.", refine_instruction).strip()
            new_func = strip_code_fence(new_func, "python")
            session.setdefault('components', {})[target] = new_func
            if artifact.get("type") in VISUAL_COMPONENT_TYPES and supports_library_renderer(artifact):
                artifact["render_mode"] = "generated_code"
            session['needs_script_update'] = True
        elif target == "LAYOUT":
            refine_instruction = f"Target Language: {lang}\nUpdate the layout JSON based on: {new_prompt}\n\nCurrent Layout: {json.dumps(session['template'], indent=2)}"
            with llm_operation_context(job_id=base_job_id, operation="refinement_layout", settings=settings, provider=provider, model_name=model_name):
                res = call_llm(provider, model_name, "Expert Information Designer specialized in JSON layouts.", refine_instruction).strip()
            res = extract_json_payload(res, "[", "]")
            try:
                session['template'] = normalize_template(json.loads(res))
                session['needs_script_update'] = True
            except:
                pass
        elif target == "SETTINGS":
            refine_instruction = f"Update the report settings JSON based on: {new_prompt}\n\nCurrent Settings: {json.dumps(settings, indent=2)}\n\nFormat: {{\"title\": str, \"font\": str, \"font_size\": int, \"alignment\": \"L\"|\"C\"|\"R\"|\"J\", \"line_height\": int, \"language\": str}}"
            with llm_operation_context(job_id=base_job_id, operation="refinement_settings", settings=settings, provider=provider, model_name=model_name):
                res = call_llm(provider, model_name, "Expert Designer specialized in document settings.", refine_instruction).strip()
            res = extract_json_payload(res, "{", "}")
            try:
                new_settings = json.loads(res)
                current_settings = session.get('settings', {
                    "title": "Relatório Analítico",
                    "font": "Arial",
                    "font_size": 10,
                    "alignment": "J",
                    "line_height": 5,
                    "language": "Portuguese"
                })

                old_lang = current_settings.get('language')
                new_lang = new_settings.get('language')

                current_settings.update(new_settings)
                session['settings'] = current_settings
                session['needs_script_update'] = True

                if new_lang and old_lang and new_lang != old_lang:
                    translated = translate_workspace(session, new_lang, job_id=base_job_id)
                    if translated:
                        session['artifacts'] = translated['artifacts']
                        session['components'] = translated['components']
                        if "Relatório" in current_settings['title'] or "Report" in current_settings['title']:
                            pass
            except:
                pass
        else:
            session['needs_full_regeneration'] = True
        return session
