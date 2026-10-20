"""Narrative text generation and fitting texts into their layout slots."""

import json
import logging
import time

from app.utils.layout import normalize_template
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.services.llm import client
from app.services.llm.client import llm_operation_context
from app.services.llm.components import slot_by_artifact_id
from app.services.llm.errors import MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMContractError, LLMTimeoutError
from app.services.llm.parsing import parse_llm_text_map
from app.services.llm.prompts import load_prompt
logger = logging.getLogger(__name__)


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
                texts = parse_llm_text_map(client.call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
                missing = {artifact["id"] for artifact in text_artifacts} - texts.keys()
                if missing:
                    raise LLMContractError("text_generation", f"Missing text for components: {', '.join(sorted(missing))}")
                return texts
            except LLMContractError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                user_prompt += f"\n\nCorrect the previous response: {exc}. Include every requested component ID."
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
                response = client.call_llm(provider, model_name, system_instruction, user_prompt)
                payload = parse_llm_text_map(response)
                for key, value in payload.items():
                    if key in texts_map and isinstance(value, str) and value.strip():
                        texts_map[key] = value.strip()
                return texts_map
            except (MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMTimeoutError):
                raise
            except Exception as e:
                logger.warning("text condensation failed attempt=%s error=%s", attempt + 1, str(e))
                time.sleep(2)

        return texts_map
