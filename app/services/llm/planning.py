"""Report planning: artifacts, document insights, layout template, data preprocessing, title."""

import json
import logging
import time

from app.utils.component_library import presets_prompt_summary
from app.utils.visual_contracts import DataBinding
from app.utils.layout import normalize_template
from app.utils.pagination import paginate_artifacts
from app.utils.page_layouts import compact_report_pages
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.utils.template_library import build_layout_from_template
from app.services.llm import client
from app.services.llm.client import call_llm_with_pdf, llm_operation_context
from app.services.llm.errors import MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMContractError, LLMTimeoutError
from app.services.llm.parsing import parse_llm_artifacts, parse_llm_document_insights, parse_llm_preprocessing_plan, strip_code_fence
from app.services.llm.prompts import load_prompt
logger = logging.getLogger(__name__)


ADDED_NARRATIVE_REASON = "Added by the application because the plan had no narrative text."
NARRATIVE_LABELS = {
    "portuguese": ("Resumo executivo", "Conclusão"),
    "english": ("Executive summary", "Conclusion"),
    "spanish": ("Resumen ejecutivo", "Conclusión"),
}


def ensure_narrative_sections(artifacts: list, settings: dict | None) -> tuple[list, list[str]]:
    """Add a summary and a conclusion when a planned report has visuals but no text at all.

    Smaller models sometimes plan charts only, which yields a PDF with almost no words.
    """
    visible = [a for a in artifacts if isinstance(a, dict) and a.get("type") not in {"metric", "text"}]
    if not visible or any(isinstance(a, dict) and a.get("type") == "text" for a in artifacts):
        return artifacts, []
    language = str((settings or {}).get("language") or "Portuguese").lower()
    summary_label, conclusion_label = NARRATIVE_LABELS.get(language, NARRATIVE_LABELS["english"])
    pages = [a["page"] for a in visible if isinstance(a.get("page"), int) and not isinstance(a.get("page"), bool)] or [1]
    used_ids = {str(a.get("id")) for a in artifacts}
    added = []
    for artifact_id, label, page in (("kpi_resumo", summary_label, min(pages)),
                                     ("kpi_conclusao", conclusion_label, max(pages))):
        if artifact_id in used_ids:
            continue
        artifacts = artifacts + [{"id": artifact_id, "type": "text", "indicator": label, "page": page, "columns": [],
                                  "reason": ADDED_NARRATIVE_REASON}]
        added.append(artifact_id)
    return artifacts, added


def generate_artifacts(
    all_metadata: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    template_context: dict = None,
    job_id: str | None = None,
    semantic_model: dict | None = None,
    page_layouts: list | None = None,
    require_metrics: bool = False,
) -> list:
    with llm_operation_context(job_id=job_id, operation="artifact_plan", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_ARTIFACTS.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        layout_constraints = ""
        if template_context:
            layout_constraints = (
                "Selected Layout Template:\n"
                f"{json.dumps(template_context, indent=2)}\n\n"
                "Use this blueprint as design guidance across the planned pages. Slot counts describe the entire blueprint, "
                "not the report's total capacity. Repeat or adapt its pages to the requested report length. "
                "Metric artifacts are not directly rendered.\n\n"
            )
        visual_catalog = json.dumps(presets_prompt_summary(), indent=2, ensure_ascii=False)
        semantic_context = ""
        if semantic_model:
            semantic_context = (
                f"Dataset Roles and Relationships:\n{json.dumps(semantic_model, ensure_ascii=False)}\n\n"
                "`verified_relationships` were checked against the data: each `from` row matches at most one `to` row, "
                "so joining from -> to keeps the row count of `from` and does not multiply totals. To combine tables in a visual, "
                "set `data_binding.source` to the `from` dataset and add `data_binding.joins` entries "
                "{\"source\": <to dataset>, \"left_on\": <from column>, \"right_on\": <to column>}; then reference joined columns as "
                "`<to dataset>:<column>`. Use only verified relationships for joins. `relationships` are name-only candidates, "
                "not verified joins: check key uniqueness, types and grain before combining them in code, and avoid many-to-many joins. "
                "Use each dataset's exact filename as its dataframe key.\n\n"
            )
        requested_pages = (settings or {}).get("page_count")
        layout_catalog = ""
        if page_layouts:
            layout_catalog = (
                f"Available Page Layouts:\n{json.dumps(page_layouts, ensure_ascii=False)}\n\n"
                "Choose a `page_layout_id` from this catalog for each page and repeat that ID on its visible components. "
                "Different pages may use different template bases. Match text and graphic counts to the selected slots; "
                "use their dimensions to choose readable visuals. Prefer filling 4 to 6 slots on a page when the evidence supports it. "
                "Keep summaries and conclusions alongside supporting evidence, without standalone title or closing pages.\n\n"
            )
        report_length = (
            f"Report Length: exactly {requested_pages} pages. Use every page from 1 to {requested_pages}."
            if requested_pages is not None else
            "Report Length: automatic. Use the fewest useful pages (1 to 50), grouping related components to fill the available template slots. "
            "The application will consolidate sparse pages when the content fits a template."
        )
        user_prompt = (
            f"Target Language: {lang}\n{report_length}\n"
            "Assign an integer `page` to every visible component. Use at most 6 visible components per page. "
              "Keep the summary on page 1 and the conclusion on the last page.\n"
            f"{layout_constraints}"
            f"{layout_catalog}"
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
                    has_metrics = any(isinstance(a, dict) and a.get("type") == "metric" for a in artifacts)
                    if require_metrics and not has_metrics and attempt < 2:
                        raise LLMContractError("artifact_plan", "The plan has no metric artifacts. Include at least 3 metrics "
                                               "computed from real columns so the narrative can cite numbers", field="type")
                    artifacts, _ = ensure_narrative_sections(artifacts, settings)
                    try:
                        visible = paginate_artifacts(artifacts, (settings or {}).get("page_count"))
                        if page_layouts:
                            known_layouts = {design["id"] for design in page_layouts}
                            for item in visible:
                                hint = item.get("page_layout_id")
                                if hint is not None and (not isinstance(hint, str) or hint not in known_layouts):
                                    raise ValueError("Choose page_layout_id only from Available Page Layouts.")
                            if requested_pages is None:
                                visible = compact_report_pages(visible, page_layouts, (template_context or {}).get("template_id"))
                    except ValueError as exc:
                        raise LLMContractError("artifact_plan", str(exc), field="page") from exc
                    pages = {item["id"]: item for item in visible}
                    for artifact in artifacts:
                        page = pages.get(artifact["id"], {})
                        if page.get("page") is not None:
                            artifact["page"] = page["page"]
                        if page.get("page_layout_id") is not None:
                            artifact["page_layout_id"] = page["page_layout_id"]
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


def generate_template(artifacts: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, template_id: str = None, owner_id=None, page_count: int | None = None) -> list:
    return normalize_template(build_layout_from_template(artifacts, template_id, owner_id, page_count=page_count))


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
        # Missing means requested: the pipeline must not drop a transformation the user may have asked for.
        "requested_by_user": payload.get("requested_by_user") is not False,
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
    columns: dict | None = None,
) -> dict:
    with llm_operation_context(job_id=job_id, operation="data_preprocessing", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_DATA_FILTER.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        user_prompt = (
            f"Target Language: {lang}\n"
            f"User Goal:\n{prompt}\n\n"
            + (f"Columns by Dataframe Key:\n{json.dumps(columns, ensure_ascii=False)}\n\n" if columns else "")
            + f"Raw Data Artifacts:\n{all_metadata}"
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


PREPROCESSING_ERROR_CHARS = 4000


def fix_data_filter(
    current_code: str,
    error: str,
    all_metadata: str,
    dataframe_keys: list,
    prompt: str = "",
    description: str = "",
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    settings: dict = None,
    job_id: str | None = None,
    columns: dict | None = None,
) -> str:
    """Ask the model to repair a preprocessing function that failed in the sandbox."""
    with llm_operation_context(job_id=job_id, operation="preprocessing_fix", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("FIX_DATA_FILTER.md")
        user_prompt = (
            f"User Goal:\n{prompt}\n\n"
            f"Intended Preprocessing:\n{description}\n\n"
            # The end of a traceback names the failing line and exception.
            f"Reported Error:\n{(error or '')[-PREPROCESSING_ERROR_CHARS:]}\n\n"
            f"Current Failing Function:\n{current_code}\n\n"
            f"Available Dataframe Keys:\n{json.dumps(dataframe_keys, ensure_ascii=False)}\n\n"
            f"Columns by Dataframe Key:\n{json.dumps(columns or {}, ensure_ascii=False)}\n\n"
            f"Raw Data Artifacts:\n{all_metadata}"
        )
        last_error: LLMContractError | None = None
        for _ in range(2):
            code = strip_code_fence(client.call_llm(provider, model_name, system_instruction, user_prompt), "python").strip()
            if "def preprocess_dataframes" in code or "def filter_dataframes" in code:
                return code
            last_error = LLMContractError(
                "preprocessing_fix",
                "The repaired code must define preprocess_dataframes(dataframes)",
                field="code",
                provider=provider,
                model_name=model_name,
                raw_excerpt=code[:500],
            )
        raise last_error


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
