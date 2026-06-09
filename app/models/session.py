from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


CURRENT_SESSION_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    original_filename: str = ""
    path: str = ""
    size_bytes: int | None = None
    mime_type: str | None = None
    artifact: dict[str, Any] | None = None
    raw_artifact: dict[str, Any] | None = None
    preprocessed_path: str | None = None
    preprocessing_description: str | None = None
    preprocessing_impact: dict[str, Any] | None = None
    filtered_path: str | None = None
    filter_description: str | None = None
    filter_impact: dict[str, Any] | None = None

    @model_validator(mode="after")
    def migrate_artifacts(self):
        if self.raw_artifact is None and self.artifact is not None:
            self.raw_artifact = self.artifact
        if self.artifact is None and self.raw_artifact is not None:
            self.artifact = self.raw_artifact
        if self.preprocessed_path is None and self.filtered_path is not None:
            self.preprocessed_path = self.filtered_path
        if self.preprocessing_description is None and self.filter_description is not None:
            self.preprocessing_description = self.filter_description
        if self.preprocessing_impact is None and self.filter_impact is not None:
            self.preprocessing_impact = self.filter_impact
        return self


class ReportSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = ""
    font: str = "Arial"
    font_size: int = 10
    alignment: str = "J"
    line_height: int = 5
    language: str = "Portuguese"
    selected_template_id: str | None = None
    visual_theme_id: str | None = "executive_clean"
    chart_style_id: str | None = "modern"
    chart_palette: list[str] = Field(default_factory=list)
    visual_instructions: str = ""
    include_data_samples: bool = False
    llm_call_budget: int = 0
    llm_timeout_seconds: int = 45
    llm_fallback_provider: str = ""
    llm_fallback_model_name: str = ""
    llm_cache_enabled: bool = True

    @model_validator(mode="after")
    def normalize_alignment(self):
        if not self.alignment:
            self.alignment = "J"
        return self


class DataFilterState(BaseModel):
    model_config = ConfigDict(extra="allow")

    needs_filter: bool = False
    needs_preprocessing: bool = False
    description: str = ""
    code: str = ""

    @model_validator(mode="after")
    def migrate_preprocessing_flags(self):
        if self.needs_filter and not self.needs_preprocessing:
            self.needs_preprocessing = True
        if self.needs_preprocessing and not self.needs_filter:
            self.needs_filter = True
        return self


class ReportSession(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = CURRENT_SESSION_SCHEMA_VERSION
    job_id: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    status: str = ""
    current_step: str = ""
    progress: int = 0
    error: dict[str, Any] | None = None
    stage_history: list[dict[str, Any]] = Field(default_factory=list)
    run_sequence: int = 0
    active_run_id: int = 0
    active_run_source: str = ""
    run_history: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    files: list[SessionFile] = Field(default_factory=list)
    initial_prompt: str = ""
    current_goal: str = ""
    is_exploratory: bool = False
    refinement_history: list[str] = Field(default_factory=list)
    refinement_messages: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    artifact_validation: list[dict[str, Any]] = Field(default_factory=list)
    template: list[dict[str, Any]] = Field(default_factory=list)
    components: dict[str, str] = Field(default_factory=dict)
    calculated_metrics: dict[str, Any] = Field(default_factory=dict)
    document_insights: dict[str, Any] = Field(default_factory=dict)
    semantic_model: dict[str, Any] = Field(default_factory=dict)
    settings: ReportSettings = Field(default_factory=ReportSettings)
    llm_usage: dict[str, Any] = Field(default_factory=dict)
    script: str = ""
    script_path: str | None = None
    pdf_path: str | None = None
    provider: str = "deepseek"
    model_name: str = "deepseek-v4-flash"
    data_filter: DataFilterState = Field(default_factory=DataFilterState)
    data_preprocessing: DataFilterState = Field(default_factory=DataFilterState)
    pdf_version: int = 0
    retry_count: int = 0
    explore_history: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_payload(cls, value):
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, dict):
            payload["artifacts"] = [
                artifact for artifact in artifacts.values()
                if isinstance(artifact, dict)
            ]

        template = payload.get("template")
        if isinstance(template, dict):
            payload["template"] = [
                item for item in template.values()
                if isinstance(item, dict)
            ]

        if payload.get("settings") is None:
            payload["settings"] = {}
        if payload.get("data_filter") is None:
            payload["data_filter"] = {}
        if payload.get("data_preprocessing") is None:
            payload["data_preprocessing"] = payload.get("data_filter") or {}

        return payload

    @model_validator(mode="after")
    def migrate_session(self):
        self.schema_version = CURRENT_SESSION_SCHEMA_VERSION
        if not self.current_goal:
            self.current_goal = self.initial_prompt
        if not self.refinement_messages and self.refinement_history:
            self.refinement_messages = [
                {"role": "user", "content": item, "kind": "legacy"}
                for item in self.refinement_history
                if str(item).strip()
            ]
        if not self.provider:
            self.provider = "deepseek"
        if not self.model_name:
            self.model_name = "deepseek-v4-flash"
        self.run_sequence = int(self.run_sequence or 0)
        self.active_run_id = int(self.active_run_id or 0)
        if not self.active_run_source and self.active_run_id:
            self.active_run_source = "pipeline"
        if self.data_preprocessing.needs_preprocessing or self.data_preprocessing.description or self.data_preprocessing.code:
            self.data_filter = self.data_preprocessing
        elif self.data_filter.needs_filter or self.data_filter.description or self.data_filter.code:
            self.data_preprocessing = self.data_filter
        return self


def normalize_session(data: dict | None, job_id: str | None = None) -> dict | None:
    if data is None:
        return None
    payload = dict(data)
    if job_id and not payload.get("job_id"):
        payload["job_id"] = job_id
    session = ReportSession.model_validate(payload)
    return session.model_dump(mode="json")
