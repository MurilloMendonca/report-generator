"""Strict parsing of model output: JSON contracts, code fences and metric values."""

import json
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.services.llm.errors import LLMContractError


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
        start = text.find(opening)
        try:
            _payload, end = json.JSONDecoder().raw_decode(text[start:])
            return text[start:start + end]
        except json.JSONDecodeError:
            pass
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
    if isinstance(value, float) and not math.isfinite(value):
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
        if value.casefold() in {"", "n/a", "nan", "inf", "infinity", "null", "none", "undefined"}:
            return "N/A"
        return "N/A" if re.match(r"^(error|erro)\s*:", value, re.IGNORECASE) else value
    return value
