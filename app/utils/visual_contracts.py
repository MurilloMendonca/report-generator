"""Visual contracts shared by the catalog, planner, editor and sandbox runtime.

Keep this module independent of the application: its source is also bundled into
the sandbox's renderer module so the exact same validation runs there.
"""

from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


Aggregation = Literal["sum", "mean", "median", "min", "max", "count"]
ColumnName = Annotated[str, Field(min_length=1)]
ColumnRole = ColumnName | Annotated[list[ColumnName], Field(min_length=1)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class VisualOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    title: str | None = None
    orientation: Literal["horizontal", "vertical"] | None = None
    top_n: int | None = Field(default=None, ge=1, le=10000)
    show_values: bool | None = None
    aggregate_others: bool | None = None
    show_markers: bool | None = None
    rolling_average: bool | None = None
    rolling_window: int | None = Field(default=None, ge=1, le=10000)
    date_granularity: Literal["day", "week", "month", "quarter", "year"] | None = None
    hide_x_label: bool | None = None
    show_trendline: bool | None = None
    show_labels: bool | None = None
    max_points: int | None = Field(default=None, ge=1, le=10000)
    show_percent: bool | None = None
    target: float | None = Field(default=None, gt=0)
    max_value: float | None = Field(default=None, gt=0)
    show_center_value: bool | None = None
    show_totals: bool | None = None
    striped_rows: bool | None = None
    sort_desc: bool | None = None
    sort_by: str | None = None
    format: str | None = None
    labels: dict[str, str] | None = None
    axis_labels: dict[str, str] | None = None
    legend_title: str | None = None
    color: Color | None = None
    line_color: Color | None = None
    bar_color: Color | None = None

    @field_validator("date_granularity", mode="before")
    @classmethod
    def normalize_granularity(cls, value):
        aliases = {"daily": "day", "monthly": "month", "weekly": "week", "annual": "year", "yearly": "year"}
        return aliases.get(value, value) if isinstance(value, str) else value


class VisualFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    column: ColumnName
    operator: Literal["==", "eq", "=", "!=", "ne", ">", "gt", ">=", "gte", "<", "lt", "<=", "lte", "in", "isin", "contains"] = Field(default="==", validation_alias=AliasChoices("operator", "op"))
    value: str | int | float | bool | list[str | int | float | bool] | None = None


class DataBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: ColumnName | None = None
    roles: dict[str, ColumnRole] = Field(default_factory=dict)
    aggregation: Aggregation = "sum"
    sort: Literal["asc", "desc", "none"] = "none"
    filters: list[VisualFilter] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=10000)


class VisualRole(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    kind: Literal["numeric", "categorical", "temporal", "any"]
    required: bool
    multiple: bool = False


COMMON_VISUAL_OPTIONS = {"title", "labels", "axis_labels", "format"}
PRESET_VISUAL_OPTIONS = {
    "bar_ranked": {"orientation", "top_n", "show_values", "aggregate_others", "color", "bar_color"},
    "bar_grouped": {"orientation", "top_n", "show_values", "aggregate_others", "color", "bar_color", "legend_title"},
    "line_timeseries": {"show_markers", "rolling_average", "rolling_window", "show_values", "date_granularity", "hide_x_label", "color", "line_color", "legend_title"},
    "scatter_relationship": {"show_trendline", "show_labels", "max_points", "legend_title", "color"},
    "radial_donut": {"top_n", "show_percent", "aggregate_others"},
    "radial_gauge": {"target", "max_value", "show_center_value"},
    "table_summary": {"top_n", "show_totals", "striped_rows", "sort_by", "sort_desc"},
    "table_detail": {"top_n", "striped_rows", "sort_by", "sort_desc"},
}


def visual_options_schema(preset_id):
    schema = VisualOptions.model_json_schema()
    allowed = COMMON_VISUAL_OPTIONS | PRESET_VISUAL_OPTIONS.get(preset_id, set())
    schema["properties"] = {key: value for key, value in schema["properties"].items() if key in allowed}
    return schema
