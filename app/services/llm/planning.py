"""Report planning: artifacts, document insights, layout template, data preprocessing, title."""

import json
import logging
import time

from app.utils.component_library import presets_prompt_summary
from app.utils.visual_contracts import DataBinding
from app.utils.layout import normalize_template
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.utils.template_library import build_layout_from_template
from app.services.llm import client
from app.services.llm.client import call_llm_with_pdf, llm_operation_context
from app.services.llm.errors import MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMContractError, LLMTimeoutError
from app.services.llm.parsing import parse_llm_artifacts, parse_llm_document_insights, parse_llm_preprocessing_plan, strip_code_fence
from app.services.llm.prompts import load_prompt
logger = logging.getLogger(__name__)


def generate_artifacts(
    all_metadata: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    template_context: dict = None,
    job_id: str | None = None,
    semantic_model: dict | None = None,
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
        semantic_context = ""
        if semantic_model:
            semantic_context = (
                f"Dataset Roles and Candidate Relationships:\n{json.dumps(semantic_model, ensure_ascii=False)}\n\n"
                "Relationships are candidates based on column names, not verified joins. "
                "Check key uniqueness, compatible types, and data grain before combining tables; "
                "avoid many-to-many joins that multiply metric values. Use each dataset's exact filename as its dataframe key.\n\n"
            )
        user_prompt = (
            f"Target Language: {lang}\n"
            f"{layout_constraints}"
            f"Available Visual Component Presets:\n{visual_catalog}\n\n"
            f"Shared Data Binding JSON Schema:\n{json.dumps(DataBinding.model_json_schema(), ensure_ascii=False)}\n\n"
            f"{semantic_context}"
            f"Data Artifacts:\n{all_metadata}\n\n"
            f"User's Initial Prompt: {prompt}"
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                artifacts = parse_llm_artifacts(client.call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
                if isinstance(artifacts, list) and len(artifacts) > 0:
                    return artifacts
                raise LLMContractError("artifact_plan", "The artifact plan must not be empty")
            except LLMContractError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                user_prompt += f"\n\nCorrect the previous response: {exc}. Return only the required JSON list."
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
                return parse_llm_document_insights(client.call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
            except LLMContractError as exc:
                if not exc.retryable:
                    raise
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
            except LLMContractError as exc:
                if not exc.retryable:
                    raise
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


def generate_template(artifacts: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, template_id: str = None, owner_id=None) -> list:
    return normalize_template(build_layout_from_template(artifacts, template_id, owner_id))


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
                payload = parse_llm_preprocessing_plan(client.call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
                return normalize_preprocessing_payload(payload)
            except LLMContractError as e:
                if not e.retryable:
                    raise
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
            title = client.call_llm(provider, model_name, system_instruction, user_prompt).strip()
            title = strip_code_fence(title).strip().strip('"').strip("'")
            title = " ".join(title.split())
            if title:
                return title
        except (MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMTimeoutError):
            raise
        except Exception as e:
            logger.warning("title generation failed error=%s", str(e))

        return fallback_report_title(prompt)


def fallback_report_title(prompt: str) -> str:
    clean_prompt = " ".join((prompt or "").split())
    if not clean_prompt:
        return "Relatório Analítico"
    return clean_prompt
