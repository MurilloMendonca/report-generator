from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class SettingsChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=300)
    font: Literal["Arial", "Helvetica", "Times", "Courier"] | None = None
    font_size: int | None = Field(default=None, ge=6, le=24, strict=True)
    alignment: Literal["L", "C", "R", "J"] | None = None
    line_height: int | None = Field(default=None, ge=1, le=20, strict=True)
    language: str | None = Field(default=None, min_length=1, max_length=80)
    visual_theme_id: str | None = None
    chart_style_id: str | None = None
    chart_palette: list[HexColor] | None = Field(default=None, max_length=12)


class VisualOptionsChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    color: HexColor | None = None
    line_color: HexColor | None = None
    bar_color: HexColor | None = None
    orientation: Literal["horizontal", "vertical"] | None = None
    show_values: bool | None = None
    show_labels: bool | None = None
    show_markers: bool | None = None
    show_trendline: bool | None = None
    hide_x_label: bool | None = None
    labels: dict[str, str] | None = None
    top_n: int | None = Field(default=None, ge=1, le=100)
    max_points: int | None = Field(default=None, ge=10, le=10000)


class LayoutChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    id: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1, le=100)
    x: float | None = Field(default=None, ge=0, le=200)
    y: float | None = Field(default=None, ge=30, le=275)
    w: float | None = Field(default=None, ge=10, le=210)
    h: float | None = Field(default=None, ge=10, le=255)

    @model_validator(mode="after")
    def require_geometry(self):
        if not self.model_dump(exclude_none=True, exclude={"id"}):
            raise ValueError("A layout update must change at least one coordinate")
        return self


class RefinementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["visual", "component", "layout", "settings", "data", "report", "clarify"]
    scope: Literal["report", "components"]
    component_ids: list[str] = Field(default_factory=list)
    instruction: str = Field(min_length=1, max_length=6000)
    settings_changes: SettingsChanges = Field(default_factory=SettingsChanges)
    visual_options: VisualOptionsChanges = Field(default_factory=VisualOptionsChanges)
    layout_updates: list[LayoutChanges] = Field(default_factory=list)
    clarification_question: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("instruction", "clarification_question")
    @classmethod
    def require_nonblank_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Text must not be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_action(self):
        settings = self.settings_changes.model_dump(exclude_none=True)
        options = self.visual_options.model_dump(exclude_none=True)
        if len(set(self.component_ids)) != len(self.component_ids):
            raise ValueError("Duplicate component IDs")
        if (self.scope == "components") != bool(self.component_ids):
            raise ValueError("Component scope requires IDs; report scope must not contain IDs")
        if self.intent in {"data", "report", "settings", "clarify"} and self.scope != "report":
            raise ValueError("This intent requires report scope")
        if self.intent == "component" and self.scope != "components":
            raise ValueError("Component edits require explicit component IDs")
        if self.intent == "clarify":
            if not self.clarification_question or settings or options or self.layout_updates:
                raise ValueError("Clarification requires a question and no changes")
        elif self.clarification_question:
            raise ValueError("Questions must use the clarify intent")
        if options and self.intent != "visual":
            raise ValueError("Visual options require visual intent")
        if self.layout_updates and self.intent != "layout":
            raise ValueError("Layout changes require layout intent")
        if self.intent == "layout" and (not self.layout_updates or settings):
            raise ValueError("Layout intent requires only coordinate updates")
        if self.intent == "settings" and not settings:
            raise ValueError("Settings intent requires changes")
        if self.intent == "component" and settings:
            raise ValueError("Component edits cannot change global settings")
        if self.intent == "visual":
            if not settings and not options:
                raise ValueError("Visual intent requires concrete styling changes")
            if settings.keys() - {"visual_theme_id", "chart_style_id", "chart_palette"}:
                raise ValueError("Visual intent cannot change document settings")
            if self.scope == "components" and settings:
                raise ValueError("Scoped visual changes cannot modify global settings")
        return self
