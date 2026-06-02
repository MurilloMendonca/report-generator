from copy import deepcopy


VISUAL_FAMILIES = {"bar", "line", "scatter", "radial", "donut", "gauge", "table", "pie", "chart", "graphic"}

CHART_STYLES = {
    "modern": {
        "id": "modern",
        "name": "Moderno",
        "description": "Grid discreto, títulos claros e marcações limpas.",
        "prompt_contract": "Use clean axes, restrained gridlines, concise labels, and avoid decorative effects.",
    },
    "minimal": {
        "id": "minimal",
        "name": "Minimalista",
        "description": "Poucos elementos visuais e foco nos dados principais.",
        "prompt_contract": "Remove non-essential spines, legends, and annotations. Keep only labels required for interpretation.",
    },
    "editorial": {
        "id": "editorial",
        "name": "Editorial",
        "description": "Mais contraste visual e títulos com leitura narrativa.",
        "prompt_contract": "Use strong but readable contrast, clear hierarchy, and selective annotations for the main point.",
    },
    "dense_dashboard": {
        "id": "dense_dashboard",
        "name": "Dashboard denso",
        "description": "Compacto, adequado para muitos blocos em uma página.",
        "prompt_contract": "Prioritize compact ticks, small legends outside the axes, fewer annotations, and strong clipping discipline.",
    },
}

VISUAL_THEMES = {
    "executive_clean": {
        "id": "executive_clean",
        "name": "Executivo limpo",
        "description": "Branco, azul forte, grid discreto e bom contraste para relatórios executivos.",
        "palette": ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"],
        "seaborn_style": "whitegrid",
        "figure_facecolor": "#ffffff",
        "axes_facecolor": "#ffffff",
        "grid_color": "#e5e7eb",
        "text_color": "#111827",
        "muted_text_color": "#6b7280",
        "table": {
            "header_bg": "#111827",
            "header_text": "#ffffff",
            "row_bg": "#ffffff",
            "alt_row_bg": "#f9fafb",
            "border": "#d1d5db",
        },
    },
    "finance_dark": {
        "id": "finance_dark",
        "name": "Financeiro sóbrio",
        "description": "Tons escuros controlados com verde e vermelho para variações.",
        "palette": ["#0f766e", "#334155", "#16a34a", "#dc2626", "#ca8a04", "#2563eb"],
        "seaborn_style": "whitegrid",
        "figure_facecolor": "#ffffff",
        "axes_facecolor": "#f8fafc",
        "grid_color": "#cbd5e1",
        "text_color": "#0f172a",
        "muted_text_color": "#475569",
        "table": {
            "header_bg": "#0f172a",
            "header_text": "#f8fafc",
            "row_bg": "#ffffff",
            "alt_row_bg": "#f1f5f9",
            "border": "#cbd5e1",
        },
    },
    "warm_editorial": {
        "id": "warm_editorial",
        "name": "Editorial quente",
        "description": "Paleta editorial com contraste moderado e leitura amigável.",
        "palette": ["#b45309", "#0f766e", "#7c2d12", "#2563eb", "#be123c", "#4d7c0f"],
        "seaborn_style": "whitegrid",
        "figure_facecolor": "#ffffff",
        "axes_facecolor": "#fffbeb",
        "grid_color": "#fde68a",
        "text_color": "#292524",
        "muted_text_color": "#78716c",
        "table": {
            "header_bg": "#78350f",
            "header_text": "#fffbeb",
            "row_bg": "#ffffff",
            "alt_row_bg": "#fffbeb",
            "border": "#fcd34d",
        },
    },
    "high_contrast": {
        "id": "high_contrast",
        "name": "Alto contraste",
        "description": "Cores fortes e legíveis, adequado para impressão e acessibilidade.",
        "palette": ["#000000", "#0072b2", "#d55e00", "#009e73", "#cc79a7", "#f0e442"],
        "seaborn_style": "whitegrid",
        "figure_facecolor": "#ffffff",
        "axes_facecolor": "#ffffff",
        "grid_color": "#9ca3af",
        "text_color": "#000000",
        "muted_text_color": "#374151",
        "table": {
            "header_bg": "#000000",
            "header_text": "#ffffff",
            "row_bg": "#ffffff",
            "alt_row_bg": "#f3f4f6",
            "border": "#6b7280",
        },
    },
    "minimal_mono": {
        "id": "minimal_mono",
        "name": "Minimal mono",
        "description": "Poucos tons, foco em linha, barras simples e tabelas.",
        "palette": ["#111827", "#4b5563", "#9ca3af", "#2563eb", "#059669", "#d97706"],
        "seaborn_style": "white",
        "figure_facecolor": "#ffffff",
        "axes_facecolor": "#ffffff",
        "grid_color": "#e5e7eb",
        "text_color": "#111827",
        "muted_text_color": "#6b7280",
        "table": {
            "header_bg": "#374151",
            "header_text": "#ffffff",
            "row_bg": "#ffffff",
            "alt_row_bg": "#f9fafb",
            "border": "#d1d5db",
        },
    },
}

COMPONENT_PRESETS = {
    "bar_ranked": {
        "id": "bar_ranked",
        "name": "Barras ranqueadas",
        "family": "bar",
        "allowed_types": ["graphic", "bar", "chart"],
        "description": "Compara categorias ordenadas por uma métrica agregada.",
        "data_roles": [
            {"name": "category", "kind": "categorical", "required": True},
            {"name": "value", "kind": "numeric", "required": True},
        ],
        "default_options": {"orientation": "horizontal", "top_n": 10, "show_values": True, "aggregate_others": True},
        "compact_guidance": "Use no máximo 6 categorias e omita rótulos longos.",
        "prompt_contract": "Aggregate by category, sort descending, keep top_n categories, and group the rest as Others when needed.",
    },
    "bar_grouped": {
        "id": "bar_grouped",
        "name": "Barras agrupadas",
        "family": "bar",
        "allowed_types": ["graphic", "bar", "chart"],
        "description": "Compara uma métrica por categoria e série.",
        "data_roles": [
            {"name": "category", "kind": "categorical", "required": True},
            {"name": "series", "kind": "categorical", "required": False},
            {"name": "value", "kind": "numeric", "required": True},
        ],
        "default_options": {"orientation": "vertical", "top_n": 8, "show_values": False, "aggregate_others": True},
        "compact_guidance": "Limite categorias e séries; prefira legenda fora do eixo.",
        "prompt_contract": "Aggregate by category and optional series. Use grouped bars only when there are few categories and series.",
    },
    "line_timeseries": {
        "id": "line_timeseries",
        "name": "Linha temporal",
        "family": "line",
        "allowed_types": ["graphic", "line", "chart"],
        "description": "Mostra tendência ao longo do tempo.",
        "data_roles": [
            {"name": "date", "kind": "temporal", "required": True},
            {"name": "value", "kind": "numeric", "required": True},
            {"name": "series", "kind": "categorical", "required": False},
        ],
        "default_options": {"show_markers": True, "rolling_average": False, "show_values": False},
        "compact_guidance": "Use poucos ticks de data e não rotule todos os pontos.",
        "prompt_contract": "Parse dates safely, aggregate by period when needed, sort chronologically, and avoid dense point labels.",
    },
    "scatter_relationship": {
        "id": "scatter_relationship",
        "name": "Dispersão",
        "family": "scatter",
        "allowed_types": ["graphic", "scatter", "chart"],
        "description": "Mostra relação entre duas métricas, com cor opcional por categoria.",
        "data_roles": [
            {"name": "x", "kind": "numeric", "required": True},
            {"name": "y", "kind": "numeric", "required": True},
            {"name": "color", "kind": "categorical", "required": False},
        ],
        "default_options": {"show_trendline": True, "show_labels": False, "max_points": 250},
        "compact_guidance": "Evite labels por ponto e use alpha para sobreposição.",
        "prompt_contract": "Plot x/y numeric values, sample or aggregate if too dense, and use an outside legend when color is used.",
    },
    "radial_donut": {
        "id": "radial_donut",
        "name": "Donut radial",
        "family": "radial",
        "allowed_types": ["graphic", "radial", "donut", "pie", "chart"],
        "description": "Mostra participação percentual para poucas categorias.",
        "data_roles": [
            {"name": "category", "kind": "categorical", "required": True},
            {"name": "value", "kind": "numeric", "required": True},
        ],
        "default_options": {"top_n": 5, "show_percent": True, "aggregate_others": True},
        "compact_guidance": "Use no máximo 4 fatias em blocos compactos.",
        "prompt_contract": "Use only for low-cardinality shares. Aggregate minor categories and keep labels readable outside or in a legend.",
    },
    "radial_gauge": {
        "id": "radial_gauge",
        "name": "Gauge radial",
        "family": "radial",
        "allowed_types": ["graphic", "radial", "gauge", "chart"],
        "description": "Mostra progresso de um valor contra meta ou total.",
        "data_roles": [
            {"name": "value", "kind": "numeric", "required": True},
            {"name": "target", "kind": "numeric", "required": False},
        ],
        "default_options": {"target": None, "show_center_value": True, "max_value": None},
        "compact_guidance": "Use texto central curto e sem legenda.",
        "prompt_contract": "Use only when there is a clear numerator and target/total. Show a central value and avoid multi-category gauges.",
    },
    "table_summary": {
        "id": "table_summary",
        "name": "Tabela resumida",
        "family": "table",
        "allowed_types": ["graphic", "table", "chart"],
        "description": "Tabela agregada curta com ranking ou resumo executivo.",
        "data_roles": [
            {"name": "dimensions", "kind": "categorical", "required": True},
            {"name": "metrics", "kind": "numeric", "required": True},
        ],
        "default_options": {"top_n": 8, "show_totals": False, "striped_rows": True},
        "compact_guidance": "Use no máximo 5 linhas e 4 colunas em blocos compactos.",
        "prompt_contract": "Create a short aggregated dataframe, sort by the main metric, format values, and render with _render_table_image when available.",
    },
    "table_detail": {
        "id": "table_detail",
        "name": "Tabela detalhada",
        "family": "table",
        "allowed_types": ["graphic", "table", "chart"],
        "description": "Mostra linhas selecionadas ou top N registros relevantes.",
        "data_roles": [
            {"name": "columns", "kind": "any", "required": True},
        ],
        "default_options": {"top_n": 10, "striped_rows": True, "sort_desc": True},
        "compact_guidance": "Evite mais de 4 colunas em blocos estreitos.",
        "prompt_contract": "Select only relevant columns and rows, format values, truncate long text, and render with _render_table_image when available.",
    },
}

DEFAULT_PRESET_BY_TYPE = {
    "graphic": "bar_ranked",
    "chart": "bar_ranked",
    "bar": "bar_ranked",
    "line": "line_timeseries",
    "scatter": "scatter_relationship",
    "pie": "radial_donut",
    "radial": "radial_donut",
    "donut": "radial_donut",
    "gauge": "radial_gauge",
    "table": "table_summary",
}


def component_library_payload() -> dict:
    return {
        "presets": list_presets(),
        "themes": list_themes(),
        "chart_styles": list_chart_styles(),
        "default_theme_id": "executive_clean",
        "default_chart_style_id": "modern",
    }


def list_presets() -> list[dict]:
    return [deepcopy(item) for item in COMPONENT_PRESETS.values()]


def list_themes() -> list[dict]:
    return [deepcopy(item) for item in VISUAL_THEMES.values()]


def list_chart_styles() -> list[dict]:
    return [deepcopy(item) for item in CHART_STYLES.values()]


def get_preset(preset_id: str | None) -> dict | None:
    if not preset_id:
        return None
    preset = COMPONENT_PRESETS.get(str(preset_id))
    return deepcopy(preset) if preset else None


def get_theme(theme_id: str | None) -> dict:
    theme = VISUAL_THEMES.get(str(theme_id or "")) or VISUAL_THEMES["executive_clean"]
    return deepcopy(theme)


def get_chart_style(style_id: str | None) -> dict:
    style = CHART_STYLES.get(str(style_id or "")) or CHART_STYLES["modern"]
    return deepcopy(style)


def infer_preset_id(artifact: dict) -> str:
    artifact_type = str((artifact or {}).get("sub_type") or (artifact or {}).get("type") or "graphic").lower()
    return DEFAULT_PRESET_BY_TYPE.get(artifact_type, "bar_ranked")


def normalize_visual_options(preset: dict | None, options: dict | None) -> dict:
    normalized = dict((preset or {}).get("default_options") or {})
    if isinstance(options, dict):
        for key, value in options.items():
            if key in normalized or key in {"title", "sort_by", "format", "target", "max_value"}:
                normalized[key] = value
    return normalized


def resolved_visual_context(artifact: dict, settings: dict | None = None) -> dict:
    settings = settings or {}
    preset = get_preset((artifact or {}).get("component_preset_id")) or get_preset(infer_preset_id(artifact))
    theme = get_theme((artifact or {}).get("visual_theme_id") or settings.get("visual_theme_id"))
    style = get_chart_style((artifact or {}).get("chart_style_id") or settings.get("chart_style_id"))
    return {
        "preset": preset,
        "theme": theme,
        "style": style,
        "options": normalize_visual_options(preset, (artifact or {}).get("visual_options")),
    }


def presets_prompt_summary() -> list[dict]:
    return [
        {
            "id": item["id"],
            "family": item["family"],
            "allowed_types": item["allowed_types"],
            "description": item["description"],
            "best_for": item["prompt_contract"],
        }
        for item in COMPONENT_PRESETS.values()
    ]
