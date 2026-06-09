from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from app.utils.component_renderers import SUPPORTED_LIBRARY_RENDER_PRESETS


VISUAL_FAMILIES = {"bar", "line", "scatter", "radial", "donut", "gauge", "table", "pie", "chart", "graphic"}
PREVIEW_CATALOG_VERSION = "2026-06-04.1"
COMPONENT_PREVIEW_DIR = Path("workspace/component_previews")

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


PRESET_EXPERIENCE = {
    "bar_ranked": {
        "best_for": ["rankings", "top N", "comparação categórica"],
        "avoid_when": ["muitas séries", "tendência temporal", "relação entre duas métricas"],
        "preview_sample": "category_rank",
        "preview_description": "Agrega categorias, ordena por valor e destaca os maiores grupos.",
    },
    "bar_grouped": {
        "best_for": ["comparação por série", "duas dimensões categóricas", "segmentos lado a lado"],
        "avoid_when": ["mais de quatro séries", "labels longos", "blocos muito estreitos"],
        "preview_sample": "grouped_category",
        "preview_description": "Compara categorias com uma série secundária sem misturar os valores.",
    },
    "line_timeseries": {
        "best_for": ["evolução temporal", "tendência", "comparação de períodos"],
        "avoid_when": ["categorias sem ordem", "poucos pontos", "valores exatos por linha"],
        "preview_sample": "timeseries",
        "preview_description": "Ordena períodos e mostra tendência sem rotular todos os pontos.",
    },
    "scatter_relationship": {
        "best_for": ["correlação", "outliers", "relação entre duas métricas"],
        "avoid_when": ["categorias textuais", "pouquíssimos pontos", "ranking executivo simples"],
        "preview_sample": "relationship",
        "preview_description": "Mostra dispersão entre duas métricas e preserva sobreposição com transparência.",
    },
    "radial_donut": {
        "best_for": ["participação percentual", "poucas categorias", "composição"],
        "avoid_when": ["alta cardinalidade", "comparação precisa", "valores negativos"],
        "preview_sample": "share",
        "preview_description": "Mostra composição com poucas fatias e texto central curto.",
    },
    "radial_gauge": {
        "best_for": ["progresso contra meta", "atingimento", "indicador único"],
        "avoid_when": ["múltiplas categorias", "sem meta clara", "série temporal"],
        "preview_sample": "gauge",
        "preview_description": "Resume progresso contra uma meta com leitura central.",
    },
    "table_summary": {
        "best_for": ["valores exatos", "ranking curto", "comparação tabular"],
        "avoid_when": ["muitas colunas", "narrativa visual", "blocos pequenos demais"],
        "preview_sample": "table",
        "preview_description": "Mantém números legíveis quando o leitor precisa comparar valores exatos.",
    },
    "table_detail": {
        "best_for": ["top registros", "auditoria", "linhas selecionadas"],
        "avoid_when": ["resumo executivo compacto", "muitas colunas longas", "PDF de uma página"],
        "preview_sample": "table",
        "preview_description": "Exibe linhas selecionadas com formatação compacta e alternância de fundo.",
    },
}


THEME_EXPERIENCE = {
    "executive_clean": {
        "best_for": ["board executivo", "relatórios gerais", "PDF limpo"],
        "avoid_when": ["alto contraste extremo", "visual editorial"],
        "accessibility_notes": "Bom contraste em fundo claro e leitura consistente em impressão.",
    },
    "finance_dark": {
        "best_for": ["finanças", "risco", "controle operacional"],
        "avoid_when": ["relatórios muito coloridos", "visual leve/editorial"],
        "accessibility_notes": "Paleta sóbria com vermelho e verde controlados; evite codificar decisão apenas por cor.",
    },
    "warm_editorial": {
        "best_for": ["narrativas", "sumários estratégicos", "relatórios menos técnicos"],
        "avoid_when": ["dashboards densos", "impressão monocromática"],
        "accessibility_notes": "Contraste moderado; revise blocos pequenos antes de usar em PDF denso.",
    },
    "high_contrast": {
        "best_for": ["acessibilidade", "impressão", "apresentação em projetor"],
        "avoid_when": ["visual sutil", "muitas séries simultâneas"],
        "accessibility_notes": "Maior contraste do catálogo; boa escolha quando legibilidade é prioridade.",
    },
    "minimal_mono": {
        "best_for": ["tabelas", "linhas", "relatórios técnicos"],
        "avoid_when": ["diferenciação por muitas categorias", "apresentações coloridas"],
        "accessibility_notes": "Pouca dependência de cor; funciona bem quando forma e ordem carregam a leitura.",
    },
}


STYLE_EXPERIENCE = {
    "modern": {
        "best_for": ["uso padrão", "relatórios executivos", "leitura equilibrada"],
        "avoid_when": ["muito pouco espaço", "visual editorial forte"],
    },
    "minimal": {
        "best_for": ["gráficos simples", "poucas séries", "PDF limpo"],
        "avoid_when": ["dados densos", "muitos eixos", "necessidade de contexto visual"],
    },
    "editorial": {
        "best_for": ["destaque narrativo", "insights principais", "apresentações"],
        "avoid_when": ["dashboards operacionais densos", "muitas anotações"],
    },
    "dense_dashboard": {
        "best_for": ["muitos componentes", "blocos pequenos", "uso operacional"],
        "avoid_when": ["storytelling visual", "gráficos com muitos labels"],
    },
}


def component_library_payload() -> dict:
    return {
        "presets": list_presets(),
        "themes": list_themes(),
        "chart_styles": list_chart_styles(),
        "preview_matrix": component_preview_matrix(),
        "default_theme_id": "executive_clean",
        "default_chart_style_id": "modern",
    }


def list_presets() -> list[dict]:
    return [_enrich_preset(item) for item in COMPONENT_PRESETS.values()]


def list_themes() -> list[dict]:
    return [_enrich_theme(item) for item in VISUAL_THEMES.values()]


def list_chart_styles() -> list[dict]:
    return [_enrich_style(item) for item in CHART_STYLES.values()]


def _required_roles(preset: dict) -> list[str]:
    return [
        role["name"]
        for role in preset.get("data_roles", [])
        if role.get("required")
    ]


def _optional_roles(preset: dict) -> list[str]:
    return [
        role["name"]
        for role in preset.get("data_roles", [])
        if not role.get("required")
    ]


def _preview_url(preset_id: str, theme_id: str = "executive_clean", style_id: str = "modern") -> str:
    return f"/api/component-library/presets/{preset_id}/preview?theme={theme_id}&style={style_id}"


def _enrich_preset(preset: dict) -> dict:
    enriched = deepcopy(preset)
    usage = PRESET_EXPERIENCE.get(enriched["id"], {})
    enriched["best_for"] = list(usage.get("best_for", []))
    enriched["avoid_when"] = list(usage.get("avoid_when", []))
    enriched["preview_sample"] = usage.get("preview_sample", enriched.get("family"))
    enriched["preview_description"] = usage.get("preview_description", enriched.get("description", ""))
    enriched["required_roles"] = _required_roles(enriched)
    enriched["optional_roles"] = _optional_roles(enriched)
    enriched["common_options"] = list((enriched.get("default_options") or {}).keys())
    enriched["preview_image_url"] = _preview_url(enriched["id"])
    enriched["supported_render_modes"] = ["generated_code"]
    if enriched["id"] in SUPPORTED_LIBRARY_RENDER_PRESETS:
        enriched["supported_render_modes"].extend(["library", "hybrid"])
    return enriched


def _enrich_theme(theme: dict) -> dict:
    enriched = deepcopy(theme)
    usage = THEME_EXPERIENCE.get(enriched["id"], {})
    enriched["best_for"] = list(usage.get("best_for", []))
    enriched["avoid_when"] = list(usage.get("avoid_when", []))
    enriched["accessibility_notes"] = usage.get("accessibility_notes", "")
    enriched["preview_image_url"] = _preview_url("bar_ranked", enriched["id"], "modern")
    return enriched


def _enrich_style(style: dict) -> dict:
    enriched = deepcopy(style)
    usage = STYLE_EXPERIENCE.get(enriched["id"], {})
    enriched["best_for"] = list(usage.get("best_for", []))
    enriched["avoid_when"] = list(usage.get("avoid_when", []))
    enriched["preview_image_url"] = _preview_url("bar_ranked", "executive_clean", enriched["id"])
    return enriched


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
            if key in normalized or key in {
                "title", "sort_by", "format", "target", "max_value",
                "labels", "axis_labels", "legend_title",
                "color", "line_color", "bar_color",
            }:
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


def component_preview_matrix(theme_id: str = "executive_clean", style_id: str = "modern") -> list[dict]:
    return [
        {
            "preset_id": preset_id,
            "theme_id": theme_id,
            "style_id": style_id,
            "image_url": _preview_url(preset_id, theme_id, style_id),
        }
        for preset_id in COMPONENT_PRESETS
    ]


def component_preview_image_path(preset_id: str, theme_id: str | None = None, style_id: str | None = None) -> str:
    preset = COMPONENT_PRESETS.get(str(preset_id or ""))
    if not preset:
        raise ValueError(f"Unknown component preset: {preset_id}")

    theme = VISUAL_THEMES.get(str(theme_id or "")) or VISUAL_THEMES["executive_clean"]
    style = CHART_STYLES.get(str(style_id or "")) or CHART_STYLES["modern"]
    COMPONENT_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    signature = {
        "version": PREVIEW_CATALOG_VERSION,
        "preset": preset["id"],
        "theme": theme["id"],
        "style": style["id"],
        "palette": theme.get("palette", []),
    }
    digest = hashlib.sha1(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    filename = f"{preset['id']}__{theme['id']}__{style['id']}__{digest}.png"
    path = COMPONENT_PREVIEW_DIR / filename
    if not path.exists():
        _render_component_preview(path, preset, theme, style)
    return str(path)


def _render_component_preview(path: Path, preset: dict, theme: dict, style: dict) -> None:
    mpl_config_dir = COMPONENT_PREVIEW_DIR / "matplotlib_config"
    mpl_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Wedge

    palette = theme.get("palette") or ["#2563eb", "#16a34a", "#f59e0b"]
    fig, ax = plt.subplots(figsize=(4.8, 2.7), dpi=120)
    fig.patch.set_facecolor(theme.get("figure_facecolor", "#ffffff"))
    ax.set_facecolor(theme.get("axes_facecolor", "#ffffff"))

    preset_id = preset["id"]
    if preset_id == "bar_grouped":
        _preview_bar_grouped(ax, palette)
    elif preset_id == "line_timeseries":
        _preview_line(ax, palette, style)
    elif preset_id == "scatter_relationship":
        _preview_scatter(ax, palette, style)
    elif preset_id == "radial_donut":
        _preview_donut(ax, palette, theme)
    elif preset_id == "radial_gauge":
        _preview_gauge(ax, palette, theme, Wedge)
    elif preset_id in {"table_summary", "table_detail"}:
        _preview_table(ax, theme)
    else:
        _preview_bar_ranked(ax, palette)

    if preset_id not in {"radial_donut", "radial_gauge", "table_summary", "table_detail"}:
        _style_preview_axes(ax, theme, style)

    title = preset.get("name", preset_id)
    ax.set_title(title, loc="left", fontsize=9, color=theme.get("text_color", "#111827"), pad=8, fontweight="bold")
    fig.tight_layout(pad=0.8)
    fig.savefig(path, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def _style_preview_axes(ax, theme: dict, style: dict) -> None:
    text_color = theme.get("text_color", "#111827")
    muted = theme.get("muted_text_color", "#6b7280")
    grid = theme.get("grid_color", "#e5e7eb")
    style_id = style.get("id")

    ax.tick_params(colors=muted, labelsize=7)
    ax.xaxis.label.set_color(text_color)
    ax.yaxis.label.set_color(text_color)
    if style_id == "minimal":
        ax.grid(False)
    else:
        ax.grid(True, color=grid, alpha=0.6, linewidth=0.8)
    for spine in ax.spines.values():
        spine.set_color(grid)
        spine.set_linewidth(0.8)
    if style_id == "dense_dashboard":
        ax.tick_params(labelsize=6)


def _preview_bar_ranked(ax, palette: list[str]) -> None:
    categories = ["Norte", "Sul", "Leste", "Oeste", "Centro"]
    values = [142, 118, 96, 82, 63]
    ax.barh(categories, values, color=palette[: len(categories)])
    ax.invert_yaxis()
    ax.set_xlabel("Receita")


def _preview_bar_grouped(ax, palette: list[str]) -> None:
    categories = ["Norte", "Sul", "Leste"]
    q1 = [82, 64, 58]
    q2 = [96, 76, 71]
    x = list(range(len(categories)))
    width = 0.34
    ax.bar([item - width / 2 for item in x], q1, width=width, color=palette[0], label="Q1")
    ax.bar([item + width / 2 for item in x], q2, width=width, color=palette[1], label="Q2")
    ax.set_xticks(x, categories)
    ax.legend(loc="upper right", fontsize=7, frameon=False)


def _preview_line(ax, palette: list[str], style: dict) -> None:
    periods = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun"]
    values = [80, 92, 88, 110, 126, 121]
    x = list(range(len(periods)))
    marker = "o" if style.get("id") != "minimal" else None
    ax.plot(x, values, color=palette[0], marker=marker, linewidth=2)
    ax.fill_between(x, values, [min(values)] * len(values), color=palette[0], alpha=0.08)
    ax.set_xticks(x, periods)
    ax.set_ylabel("Valor")


def _preview_scatter(ax, palette: list[str], style: dict) -> None:
    xs = [18, 22, 25, 29, 35, 39, 44, 48, 53, 58, 62, 67]
    ys = [41, 46, 43, 55, 58, 62, 59, 71, 73, 78, 81, 87]
    ax.scatter(xs, ys, s=38 if style.get("id") != "dense_dashboard" else 24, color=palette[0], alpha=0.76)
    ax.plot([min(xs), max(xs)], [min(ys), max(ys)], color=palette[1], linewidth=1.6, alpha=0.8)
    ax.set_xlabel("Investimento")
    ax.set_ylabel("Resultado")


def _preview_donut(ax, palette: list[str], theme: dict) -> None:
    values = [42, 27, 19, 12]
    labels = ["A", "B", "C", "D"]
    ax.pie(values, labels=labels, colors=palette[:4], startangle=90, wedgeprops={"width": 0.42, "edgecolor": "white"})
    ax.text(0, 0, "100%", ha="center", va="center", color=theme.get("text_color", "#111827"), fontsize=11, fontweight="bold")
    ax.axis("equal")


def _preview_gauge(ax, palette: list[str], theme: dict, wedge_cls) -> None:
    value = 0.72
    ax.set_xlim(-1.2, 1.2)
    ax.set_ylim(-0.15, 1.2)
    ax.axis("off")
    background = wedge_cls((0, 0), 1, 0, 180, width=0.28, facecolor=theme.get("grid_color", "#e5e7eb"), edgecolor="white")
    foreground = wedge_cls((0, 0), 1, 0, 180 * value, width=0.28, facecolor=palette[0], edgecolor="white")
    ax.add_patch(background)
    ax.add_patch(foreground)
    ax.text(0, 0.18, "72%", ha="center", va="center", color=theme.get("text_color", "#111827"), fontsize=16, fontweight="bold")
    ax.text(0, -0.02, "atingimento", ha="center", va="center", color=theme.get("muted_text_color", "#6b7280"), fontsize=8)


def _preview_table(ax, theme: dict) -> None:
    ax.axis("off")
    table_theme = theme.get("table", {})
    rows = [["Norte", "142k", "1.12"], ["Sul", "118k", "0.98"], ["Leste", "96k", "0.91"]]
    table = ax.table(
        cellText=rows,
        colLabels=["Região", "Receita", "Meta"],
        loc="center",
        cellLoc="left",
        colLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.35)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(table_theme.get("border", "#d1d5db"))
        if row == 0:
            cell.set_facecolor(table_theme.get("header_bg", "#111827"))
            cell.get_text().set_color(table_theme.get("header_text", "#ffffff"))
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor(table_theme.get("alt_row_bg", "#f9fafb") if row % 2 else table_theme.get("row_bg", "#ffffff"))
            cell.get_text().set_color(theme.get("text_color", "#111827"))
