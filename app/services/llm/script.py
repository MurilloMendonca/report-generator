"""Assembling the standalone report script, repairing it, and data-explorer scripts."""

import json

from app.utils.component_library import get_chart_style, get_theme
from app.utils.component_renderers import hybrid_component_code, library_component_code, rename_generated_component_code, supports_library_renderer
from app.utils.exploration_charts import exploration_runtime_source
from app.utils.layout import normalize_template
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.services.llm import client
from app.services.llm.client import llm_operation_context
from app.services.llm.metrics import dataframe_load_line
from app.services.llm.parsing import strip_code_fence
from app.services.llm.prompts import load_prompt


def assemble_script(
    components: dict,
    template: list,
    files_map: dict,
    output_pdf_path: str,
    settings: dict = None,
    page_format: tuple[float, float] | None = None,
    include_title: bool = True,
    normalize_layout: bool = True,
    artifacts: list[dict] | None = None,
) -> str:
    template = sorted(
        normalize_template(template) if normalize_layout else [item.copy() for item in template or []],
        key=lambda item: (item.get("page", 1), item.get("y", 0), item.get("x", 0))
    )
    # Default settings if none provided
    if not settings:
        settings = {
            "title": "Relatório Analítico",
            "font": "Arial",
            "font_size": 10,
            "alignment": "J",
            "line_height": 5
        }
    
    # fpdf2 uses standard names like 'helvetica', 'times', 'courier'
    font_family = settings.get('font', 'Arial').lower()
    if font_family == 'arial': font_family = 'helvetica'
    chart_theme = get_theme(settings.get("visual_theme_id"))
    chart_style = get_chart_style(settings.get("chart_style_id"))
    chart_palette = settings.get('chart_palette') or chart_theme.get("palette") or []
    if not isinstance(chart_palette, list):
        chart_palette = []
    
    artifacts_by_id = {
        str(artifact.get("id")): artifact
        for artifact in artifacts or []
        if isinstance(artifact, dict) and artifact.get("id")
    }

    script = [
        "import os", "os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')",
        "import json",
        "import inspect",
        "import time",
        "import pandas as pd", "import numpy as np", "import matplotlib as mpl", "import matplotlib.pyplot as plt", "import seaborn as sns",
        "from fpdf import FPDF, XPos, YPos", "import tempfile", "import textwrap", "",
        "CHART_TITLE_FONTSIZE = 9",
        "CHART_TITLE_FONTWEIGHT = 'bold'",
        "CHART_TITLE_PAD = 6",
        "CHART_LABEL_FONTSIZE = 7",
        "CHART_TICK_FONTSIZE = 6",
        "CHART_LEGEND_FONTSIZE = 6",
        "CHART_TABLE_FONTSIZE = 6",
        f"CHART_PALETTE = {json.dumps(chart_palette)}",
        f"CHART_THEME = {json.dumps(chart_theme, ensure_ascii=False)}",
        f"CHART_STYLE = {json.dumps(chart_style, ensure_ascii=False)}",
        "sns.set_theme(style=CHART_THEME.get('seaborn_style', 'whitegrid'))",
        "if CHART_PALETTE:",
        "    sns.set_palette(CHART_PALETTE)",
        "    mpl.rcParams['axes.prop_cycle'] = plt.cycler(color=CHART_PALETTE)",
        "mpl.rcParams.update({",
        "    'axes.titlesize': CHART_TITLE_FONTSIZE,",
        "    'axes.titleweight': CHART_TITLE_FONTWEIGHT,",
        "    'axes.labelsize': CHART_LABEL_FONTSIZE,",
        "    'xtick.labelsize': CHART_TICK_FONTSIZE,",
        "    'ytick.labelsize': CHART_TICK_FONTSIZE,",
        "    'legend.fontsize': CHART_LEGEND_FONTSIZE,",
        "    'figure.facecolor': CHART_THEME.get('figure_facecolor', '#ffffff'),",
        "    'axes.facecolor': CHART_THEME.get('axes_facecolor', '#ffffff'),",
        "    'axes.edgecolor': CHART_THEME.get('grid_color', '#e5e7eb'),",
        "    'axes.labelcolor': CHART_THEME.get('text_color', '#111827'),",
        "    'xtick.color': CHART_THEME.get('muted_text_color', '#6b7280'),",
        "    'ytick.color': CHART_THEME.get('muted_text_color', '#6b7280'),",
        "    'grid.color': CHART_THEME.get('grid_color', '#e5e7eb'),",
        "    'text.color': CHART_THEME.get('text_color', '#111827'),",
        "})",
        "",
        "import pdf_report_renderers.runtime as render_runtime",
        "from pdf_report_renderers.runtime import (",
        "    configure_visual_runtime, render_visual_component, render_bar_chart,",
        "    begin_component_render, component_render_outcome,",
        "    render_line_chart, render_scatter_plot, render_donut_chart, render_gauge_chart,",
        "    render_summary_table, render_detail_table, _resolved_chart_theme,",
        "    _chart_colors, _apply_chart_theme, _format_compact_number,",
        "    _top_n_with_others, _save_chart_to_pdf, _render_table_image,",
        "    _library_dataframe, _library_resolve_column, _library_resolve_columns,",
        "    _library_aggregate, _library_limit_top, _library_empty_chart,",
        ")",
        "configure_visual_runtime(CHART_THEME, CHART_PALETTE, CHART_STYLE)",
        "",
        "def _safe_pdf_text(value):",
        "    return render_runtime.safe_pdf_text(value)",
        "",
        "def _write_report_title(pdf, title, font_family):",
        "    return render_runtime.write_pdf_report_title(pdf, title, font_family, warn=render_runtime.record_render_warning)",
        "",
        "def render_text_block(pdf, text, x, y, w, h, title=None):",
        f"    return render_runtime.render_pdf_text_block(pdf, text, x, y, w, h, title=title, font_family={font_family!r},",
        f"        font_size={settings.get('font_size', 10)!r}, line_height={settings.get('line_height', 5)!r},",
        f"        alignment={settings.get('alignment', 'J')!r}, warn=render_runtime.record_render_warning)",
        ""
    ]
    library_wrapper_ids = set()
    for comp_id, func_code in components.items():
        artifact = artifacts_by_id.get(str(comp_id))
        render_mode = str((artifact or {}).get("render_mode") or "generated_code")
        if artifact and render_mode == "hybrid" and supports_library_renderer(artifact):
            script.append(rename_generated_component_code(str(comp_id), func_code))
        else:
            script.append(func_code)
            if artifact and render_mode == "library" and supports_library_renderer(artifact):
                library_wrapper_ids.add(str(comp_id))
        script.append("")

    for item in template:
        artifact = artifacts_by_id.get(str(item.get("id")))
        if not artifact or not supports_library_renderer(artifact):
            continue
        render_mode = str(artifact.get("render_mode") or "generated_code")
        if render_mode not in {"library", "hybrid"}:
            continue
        artifact_id = str(artifact.get("id"))
        if artifact_id in library_wrapper_ids:
            continue
        wrapper = hybrid_component_code(artifact) if render_mode == "hybrid" else library_component_code(artifact)
        script.append(wrapper)
        script.append("")
        library_wrapper_ids.add(artifact_id)

    script.append("def generate_report():")
    script.append("    dataframes = {}")
    script.append("    import logging")
    for original_name, path in files_map.items():
        line = dataframe_load_line(original_name, path, indent="    ")
        if line:
            script.append(line)

    if page_format:
        script.append(f"    pdf = FPDF(format=({float(page_format[0])}, {float(page_format[1])}))")
    else:
        script.append("    pdf = FPDF()")
    script.append("    pdf.set_auto_page_break(auto=False)")
    script.append("    current_page = 0")
    script.append("    component_manifest = []")
    script.append("    def ensure_page(page):")
    script.append("        nonlocal current_page")
    script.append("        while current_page < page:")
    script.append("            pdf.add_page()")
    script.append("            current_page += 1")
    if include_title:
        script.append("            if current_page == 1:")
        script.append(f"                _write_report_title(pdf, {json.dumps(settings.get('title') or 'Relatório Analítico')}, '{font_family}')")
        script.append("                pdf.ln(3)")
    else:
        script.append("            pass")
    script.append("    ensure_page(1)")
    for item in template:
        page = item.get("page", 1)
        render_mode = str((artifacts_by_id.get(str(item['id'])) or {}).get('render_mode') or 'generated_code')
        script.append(f"    ensure_page({page})")
        script.append("    component_started = time.perf_counter()")
        script.append(f"    begin_component_render({render_mode!r})")
        script.append(f"    try:")
        script.append(f"        # Provide a default 'df' for backward compatibility with older prompts/scripts")
        script.append(f"        primary_df = list(dataframes.values())[0] if dataframes else None")
        script.append(f"        try:")
        script.append(f"            inspect.signature(render_{item['id']}).bind(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']}, df=primary_df)")
        script.append("            accepts_df = True")
        script.append("        except (TypeError, ValueError):")
        script.append("            accepts_df = False")
        script.append("        if accepts_df:")
        script.append(f"            component_result = render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']}, df=primary_df)")
        script.append("        else:")
        script.append(f"            component_result = render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']})")
        preset_id = (artifacts_by_id.get(str(item['id'])) or {}).get('component_preset_id')
        script.append(f"        component_manifest.append({{**component_render_outcome(component_result), 'id': {json.dumps(item['id'])}, 'preset_id': {preset_id!r}, 'duration_ms': (time.perf_counter() - component_started) * 1000}})")
        script.append(f"    except Exception as e: ")
        script.append(f"        component_manifest.append({{**component_render_outcome(), 'id': {json.dumps(item['id'])}, 'preset_id': {preset_id!r}, 'status': getattr(e, 'render_status', 'failed'), 'code': getattr(e, 'render_code', None), 'error': str(e), 'duration_ms': (time.perf_counter() - component_started) * 1000}})")
        script.append(f"        logging.getLogger(__name__).exception('Erro no componente {json.dumps(item['id'])}')")
    script.append(f"    manifest_path = {str(output_pdf_path + '.manifest.json')!r}")
    script.append("    with open(manifest_path, 'w', encoding='utf-8') as manifest_file:")
    script.append("        json.dump({'components': component_manifest}, manifest_file, indent=2, ensure_ascii=False)")
    script.append("    failed_components = [item for item in component_manifest if item.get('status') != 'rendered']")
    script.append("    if failed_components:")
    script.append("        raise RuntimeError('Report component rendering failed: ' + ', '.join(item['id'] for item in failed_components))")
    script.append(f"    pdf.output({output_pdf_path!r})")
    script.append("")
    script.append("if __name__ == '__main__':")
    script.append("    generate_report()")
    return "\n".join(script)


def fix_script(current_script: str, error: str, all_metadata: str, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, job_id: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="script_fix", provider=provider, model_name=model_name):
        system_instruction = load_prompt("FIX_SCRIPT.md")
        user_prompt = f"Reported Error: {error}\n\nCurrent Failing Script:\n{current_script}\n\nData Artifacts:\n{all_metadata}"

        text = client.call_llm(provider, model_name, system_instruction, user_prompt)
        return strip_code_fence(text, "python")


def generate_exploration_script(all_metadata: str, query: str, files_map: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, history: list = None, job_id: str | None = None, settings: dict = None, output_directory: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="exploration_script", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("EXPLORE_DATA.md")
        recent_history = [
            {"role": message.get("role"), "content": str(message.get("content") or "")[:2000], "charts": [
                {key: chart.get(key, "") for key in ("title", "caption")}
                for chart in message.get("charts", [])
            ]}
            for message in (history or [])[-12:]
        ]
        user_prompt = (
            f"Target Language: {(settings or {}).get('language', 'Portuguese')}\n"
            f"Conversation History:\n{json.dumps(recent_history, ensure_ascii=False)}\n\n"
            f"Data Artifacts:\n{all_metadata}\n\nUser Query: {query}"
        )

        text = client.call_llm(provider, model_name, system_instruction, user_prompt)
        text = strip_code_fence(text, "python")

        # Injetar carregamento dos dataframes
        setup_code = [
            "import pandas as pd",
            "import numpy as np",
            "dataframes = {}"
        ]
        if output_directory:
            theme = get_theme((settings or {}).get("visual_theme_id"))
            palette = (settings or {}).get("chart_palette") or theme.get("palette") or []
            setup_code.append(exploration_runtime_source(output_directory, theme, palette))
        for original_name, path in files_map.items():
            line = dataframe_load_line(original_name, path)
            if line:
                setup_code.append(line)

        full_script = "\n".join(setup_code) + "\n\n" + text
        if output_directory:
            full_script += "\n\n_finish_exploration()\n"
        return full_script
