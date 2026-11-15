"""Request bodies of the report API, so the OpenAPI schema documents their fields and limits."""
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REGENERATION_SCOPES = ["texts", "graphics", "component", "all"]


class SettingsUpdate(BaseModel):
    """Report settings to change. Fields left out keep their current value."""
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=300, description="Report title; empty lets the model write one.")
    font: str | None = Field(None, max_length=40)
    font_size: int | None = Field(None, ge=6, le=24)
    alignment: Literal["", "J", "L", "C", "R"] | None = Field(None, description="Text alignment: J, L, C or R.")
    line_height: int | None = Field(None, ge=1, le=20)
    language: str | None = Field(None, max_length=40, description="Report language, for example Portuguese or English.")
    page_count: int | None = Field(None, ge=1, le=50, strict=True,
                                   description="Fixed number of pages; null lets the planner decide. Changing it replans the report.")
    selected_template_id: str | None = Field(None, max_length=100, description="Layout template applied to every page.")
    visual_theme_id: str | None = Field(None, max_length=100)
    chart_style_id: str | None = Field(None, max_length=100)
    chart_palette: list[str] | None = Field(None, max_length=20)
    visual_instructions: str | None = Field(None, max_length=4000)
    include_data_samples: bool | None = Field(None, description="Send masked sample rows to the model.")
    llm_call_budget: int | None = Field(None, ge=0, le=1000, description="Maximum model calls per run; 0 uses the server limit.")
    llm_timeout_seconds: int | None = Field(None, ge=0, le=600, description="Seconds per model call; 0 uses the server setting.")
    llm_fallback_provider: str | None = Field(None, max_length=50)
    llm_fallback_model_name: str | None = Field(None, max_length=100)
    llm_cache_enabled: bool | None = None
    provider: str | None = Field(None, max_length=50, description="Provider for later model calls.")
    model_name: str | None = Field(None, max_length=100)


class RegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(json_schema_extra={"enum": REGENERATION_SCOPES},
                       description="texts, graphics, component (with component_id) or all.")
    component_id: str | None = Field(None, max_length=200)
    settings: SettingsUpdate | None = Field(None, description="Settings saved before regenerating.")


class ArtifactsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: list[dict[str, Any]] = Field(default_factory=list, description="The full artifact plan (metrics, charts, tables, texts).")
    components: dict[str, str] = Field(default_factory=dict, description="Python code to use for some components, by artifact id.")
    defer_pipeline: bool = Field(False, description="Save without regenerating the report.")


class LayoutTemplateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field("", max_length=200)
    description: str = Field("", max_length=2000)
    slots: list[dict[str, Any]] = Field(default_factory=list, description="Blocks with page, x, y, w and h in millimetres.")
    page_count: int | None = Field(None, ge=1, le=100)


def body_fields(model: type[BaseModel], value) -> dict:
    """The fields a client sent, validated. Accepts a model (HTTP) or a dict (direct calls)."""
    if not isinstance(value, model):
        try:
            value = model.model_validate(value or {})
        except ValidationError as exc:
            if any("page_count" in error["loc"] for error in exc.errors()):
                raise HTTPException(422, detail="Informe um número inteiro de páginas entre 1 e 50, ou deixe em branco para a IA decidir.") from None
            raise HTTPException(422, detail=[{"loc": list(error["loc"]), "type": error["type"], "msg": "Valor inválido."}
                                             for error in exc.errors()]) from None
    return value.model_dump(exclude_unset=True)
