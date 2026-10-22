import json
import re
from textwrap import dedent
from pathlib import Path


SUPPORTED_LIBRARY_RENDER_PRESETS = {
    "bar_ranked",
    "bar_grouped",
    "line_timeseries",
    "scatter_relationship",
    "radial_donut",
    "radial_gauge",
    "table_summary",
    "table_detail",
}


def supports_library_renderer(preset_id: str | dict | None) -> bool:
    if isinstance(preset_id, dict):
        preset_id = preset_id.get("component_preset_id") or preset_id.get("id")
    return str(preset_id or "") in SUPPORTED_LIBRARY_RENDER_PRESETS


def runtime_api_prompt_reference(preset_id: str | None = None) -> str:
    signatures = [
        "render_visual_component(pdf, dataframes, artifact, x, y, w, h)",
        "render_bar_chart(pdf, dataframes, x, y, w, h, *, category, value, source=None, series=None, title=None, options=None)",
        "render_line_chart(pdf, dataframes, x, y, w, h, *, date, value, source=None, series=None, title=None, options=None)",
        "render_scatter_plot(pdf, dataframes, x, y, w, h, *, x_value, y_value, source=None, color=None, title=None, options=None)",
        "render_donut_chart(pdf, dataframes, x, y, w, h, *, category, value, source=None, title=None, options=None)",
        "render_gauge_chart(pdf, dataframes, x, y, w, h, *, value, source=None, target=None, title=None, options=None)",
        "render_summary_table(pdf, dataframes, x, y, w, h, *, dimensions, metrics, source=None, title=None, options=None)",
        "render_detail_table(pdf, dataframes, x, y, w, h, *, columns=None, source=None, title=None, options=None)",
    ]
    preset_hint = {
        "bar_ranked": "Prefer render_bar_chart(...) with category/value roles and options top_n, orientation, show_values, aggregate_others.",
        "bar_grouped": "Prefer render_bar_chart(...) with category/value/series roles.",
        "line_timeseries": "Prefer render_line_chart(...) with date/value and optional series roles.",
        "scatter_relationship": "Prefer render_scatter_plot(...) with x/y and optional color roles.",
        "radial_donut": "Prefer render_donut_chart(...) for low-cardinality share visuals.",
        "radial_gauge": "Prefer render_gauge_chart(...) only when a clear value and target/max exist.",
        "table_summary": "Prefer render_summary_table(...). For custom table layout, build a small dataframe and call _render_table_image(...).",
        "table_detail": "Prefer render_detail_table(...). For custom table layout, select/format columns and call _render_table_image(...).",
    }.get(str(preset_id or ""), "Prefer render_visual_component(...) first, then a focused helper call if customization is required.")
    return dedent(f"""
    Runtime API available in the final script:
    - Preferred path: call library helpers instead of recreating generic chart/table code.
    - Module alias: `render_runtime` exposes the same runtime helpers for advanced customization.
    - Common helper globals: `_chart_colors`, `_apply_chart_theme`, `_format_compact_number`, `_top_n_with_others`, `_save_chart_to_pdf`, `_render_table_image`.
      Table helper signature: `_render_table_image(pdf, dataframe, x, y, w, h, title=None, theme=None, options=None)`.
    - Advanced base helpers via `render_runtime`: `_library_dataframe`, `_library_resolve_column`, `_library_resolve_columns`, `_library_aggregate`, `_library_limit_top`, `_library_empty_chart`.

    Primary signatures:
    {chr(10).join(f"- {signature}" for signature in signatures)}

    Preset guidance:
    - {preset_hint}

    Customization rule:
    - Keep generated code small. Do not paste runtime helper bodies into the component function.
    - If the standard helper is close, prepare/transform `options`, `artifact`, or a small dataframe and call the helper.
    - For translated or friendly labels, prefer `artifact["visual_options"]["labels"]`; do not rename dataframe columns just to change axis labels, legends, or table headers.
    - Render tables through `_render_table_image(...)` after any data transformations. It measures and wraps headers/cells, allocates column widths and row heights, and keeps text inside the layout slot. Do not recreate tables with equal-width cells or truncate labels by character count.
    - Other chart helpers measure titles, legends, ticks, annotations and central values. Preserve full category names and identities; display fitting must never merge categories. Custom charts should finish with `_save_chart_to_pdf(...)`, preserving the figure aspect ratio without tight-bbox cropping.
    - For `render_mode: "library"` adapters that truly need data transformation, copy the needed dataframe, update `artifact`/`dataframes` consistently, and call `render_visual_component(...)`.
    - If the user explicitly asks for a custom table/chart, use runtime helper behavior as the base and override only the specific visual detail.
    """).strip()


def render_function_suffix(component_id: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", str(component_id or "")).strip("_")
    if not suffix:
        suffix = "component"
    if suffix[0].isdigit():
        suffix = f"kpi_{suffix}"
    return suffix


def rename_generated_component_code(component_id: str, code: str) -> str:
    suffix = render_function_suffix(component_id)
    pattern = re.compile(rf"(^\s*def\s+)render_{re.escape(suffix)}(\s*\()", re.MULTILINE)

    def replacement(match: re.Match) -> str:
        return f"{match.group(1)}render_{suffix}_generated{match.group(2)}"

    return pattern.sub(replacement, code or "", count=1)


def library_component_code(artifact: dict) -> str:
    suffix = render_function_suffix(artifact.get("id"))
    artifact_payload = json.dumps(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True),
        ensure_ascii=False,
    )
    return dedent(f"""
    def render_{suffix}(pdf, dataframes, x, y, w, h, df=None):
        artifact = json.loads({artifact_payload})
        return render_visual_component(pdf, dataframes, artifact, x, y, w, h)
    """).strip()


def hybrid_component_code(artifact: dict) -> str:
    suffix = render_function_suffix(artifact.get("id"))
    artifact_payload = json.dumps(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True),
        ensure_ascii=False,
    )
    return dedent(f"""
    def render_{suffix}(pdf, dataframes, x, y, w, h, df=None):
        artifact = json.loads({artifact_payload})
        generated = globals().get('render_{suffix}_generated')
        if generated is not None:
            try:
                import inspect
                try:
                    inspect.signature(generated).bind(pdf, dataframes, x, y, w, h, df=df)
                    accepts_df = True
                except (TypeError, ValueError):
                    accepts_df = False
                if accepts_df:
                    return generated(pdf, dataframes, x, y, w, h, df=df)
                return generated(pdf, dataframes, x, y, w, h)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Generated hybrid renderer failed for {suffix}; falling back to library renderer')
                runtime = globals().get('render_runtime')
                if runtime is not None:
                    runtime.record_component_fallback()
        try:
            return render_visual_component(pdf, dataframes, artifact, x, y, w, h)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Library renderer failed for {suffix}')
            raise
    """).strip()


def sandbox_runtime_source() -> str:
    from app.utils.component_library import COMPONENT_PRESETS, VISUAL_THEMES, CHART_STYLES

    contracts = Path(__file__).with_name("visual_contracts.py").read_text(encoding="utf-8")
    fitting_helpers = "\n\n".join(
        Path(__file__).with_name(filename).read_text(encoding="utf-8")
        for filename in ("chart_layout.py", "pdf_text.py")
    )
    catalog = (f"\nVISUAL_THEMES = {VISUAL_THEMES!r}\nCHART_STYLES = {CHART_STYLES!r}\n"
               f"PRESET_ROLES = { {key: value['data_roles'] for key, value in COMPONENT_PRESETS.items()}!r}\n"
               f"PRESET_OPTIONS = { {key: value['default_options'] for key, value in COMPONENT_PRESETS.items()}!r}\n")
    return contracts + catalog + dedent(r"""
    import os
    import re
    import tempfile
    import textwrap
    from contextlib import contextmanager

    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns

    try:
        import altair as alt
        import vl_convert as vlc
        _ALTAIR_AVAILABLE = os.environ.get('PDF_REPORT_USE_ALTAIR') == '1'
    except Exception:
        alt = None
        vlc = None
        _ALTAIR_AVAILABLE = False


    CHART_TITLE_FONTSIZE = 9
    CHART_TITLE_FONTWEIGHT = 'bold'
    CHART_TITLE_PAD = 6
    CHART_LABEL_FONTSIZE = 7
    CHART_TICK_FONTSIZE = 6
    CHART_LEGEND_FONTSIZE = 6
    CHART_TABLE_FONTSIZE = 6
    CHART_PALETTE = []
    CHART_THEME = {}
    CHART_STYLE = {}
    _STANDARD_CHART_STYLE_INSTALLED = False
    _STANDARD_CHART_STYLE_TOKEN = object()
    _COMPONENT_OUTCOME = {}


    class VisualBindingError(ValueError):
        render_status = 'invalid_binding'


    def begin_component_render(render_mode='generated_code'):
        global _COMPONENT_OUTCOME
        _COMPONENT_OUTCOME = {'status': 'rendered', 'render_mode': 'generated_code' if render_mode == 'hybrid' else render_mode,
                              'requested_mode': render_mode, 'fallback_used': False, 'warnings': []}


    def component_render_outcome(result=None):
        outcome = dict(_COMPONENT_OUTCOME)
        if isinstance(result, dict) and result.get('status') in {'rendered', 'empty', 'invalid_binding', 'failed'}:
            outcome.update({key: value for key, value in result.items() if key in {'status', 'message', 'warnings'}})
            if _COMPONENT_OUTCOME.get('status') != 'rendered':
                outcome['status'] = _COMPONENT_OUTCOME.get('status', outcome['status'])
        return outcome


    def record_component_fallback():
        _COMPONENT_OUTCOME.update(status='rendered', render_mode='library', fallback_used=True)
        _COMPONENT_OUTCOME.pop('message', None)


    @contextmanager
    def _component_visual_context(artifact):
        old_theme, old_palette, old_style = CHART_THEME, CHART_PALETTE, CHART_STYLE
        theme = VISUAL_THEMES.get(artifact.get('visual_theme_id'), old_theme)
        style = CHART_STYLES.get(artifact.get('chart_style_id'), old_style)
        palette = theme.get('palette') if artifact.get('visual_theme_id') else old_palette
        with mpl.rc_context():
            configure_visual_runtime(theme, palette, style)
            try:
                yield
            finally:
                configure_visual_runtime(old_theme, old_palette, old_style)


    def configure_visual_runtime(theme=None, palette=None, style=None):
        global CHART_THEME, CHART_PALETTE, CHART_STYLE
        CHART_THEME = dict(theme or {})
        CHART_PALETTE = list(palette or CHART_THEME.get('palette') or [])
        CHART_STYLE = dict(style or {})
        sns.set_theme(style=CHART_THEME.get('seaborn_style', 'whitegrid'))
        if CHART_PALETTE:
            sns.set_palette(CHART_PALETTE)
            mpl.rcParams['axes.prop_cycle'] = plt.cycler(color=CHART_PALETTE)
        mpl.rcParams.update({
            'axes.titlesize': CHART_TITLE_FONTSIZE,
            'axes.titleweight': CHART_TITLE_FONTWEIGHT,
            'axes.labelsize': CHART_LABEL_FONTSIZE,
            'xtick.labelsize': CHART_TICK_FONTSIZE,
            'ytick.labelsize': CHART_TICK_FONTSIZE,
            'legend.fontsize': CHART_LEGEND_FONTSIZE,
            'figure.facecolor': CHART_THEME.get('figure_facecolor', '#ffffff'),
            'axes.facecolor': CHART_THEME.get('axes_facecolor', '#ffffff'),
            'axes.edgecolor': CHART_THEME.get('grid_color', '#e5e7eb'),
            'axes.labelcolor': CHART_THEME.get('text_color', '#111827'),
            'xtick.color': CHART_THEME.get('muted_text_color', '#6b7280'),
            'ytick.color': CHART_THEME.get('muted_text_color', '#6b7280'),
            'grid.color': CHART_THEME.get('grid_color', '#e5e7eb'),
            'text.color': CHART_THEME.get('text_color', '#111827'),
        })
        _install_standard_chart_style()


    def _resolved_chart_theme():
        return dict(CHART_THEME or {})


    def _chart_colors(n):
        palette = CHART_PALETTE or CHART_THEME.get('palette') or ['#2563eb', '#16a34a', '#f59e0b', '#dc2626']
        if n <= len(palette):
            return palette[:n]
        return [palette[i % len(palette)] for i in range(n)]


    def _monochrome_charts():
        return CHART_THEME.get('color_mode') == 'monochrome'


    def _apply_chart_theme(fig, ax=None, theme=None):
        theme = theme or CHART_THEME
        fig.patch.set_facecolor(theme.get('figure_facecolor', '#ffffff'))
        axes = fig.axes if ax is None else [ax]
        for axis in axes:
            axis.set_facecolor(theme.get('axes_facecolor', '#ffffff'))
            axis.tick_params(colors=theme.get('muted_text_color', '#6b7280'))
            axis.xaxis.label.set_color(theme.get('text_color', '#111827'))
            axis.yaxis.label.set_color(theme.get('text_color', '#111827'))
            axis.title.set_color(theme.get('text_color', '#111827'))
            minimal = CHART_STYLE.get('id') == 'minimal'
            if minimal:
                axis.grid(False)
            else:
                axis.grid(color=theme.get('grid_color', '#e5e7eb'), alpha=0.35 if CHART_STYLE.get('id') == 'editorial' else 0.7)
            for spine in axis.spines.values():
                spine.set_color(theme.get('grid_color', '#e5e7eb'))
                spine.set_visible(not minimal)
            if CHART_STYLE.get('id') == 'dense_dashboard':
                axis.tick_params(labelsize=5)
            elif CHART_STYLE.get('id') == 'editorial':
                axis.title.set_fontsize(10)
                axis.title.set_fontweight('bold')
        return fig


    def _format_compact_number(value):
        try:
            value = float(value)
        except Exception:
            return str(value)
        abs_value = abs(value)
        if abs_value >= 1_000_000_000:
            return f'{value / 1_000_000_000:.1f}B'
        if abs_value >= 1_000_000:
            return f'{value / 1_000_000:.1f}M'
        if abs_value >= 1_000:
            return f'{value / 1_000:.1f}K'
        if value.is_integer():
            return f'{value:.0f}'
        return f'{value:.1f}'


    def _format_visual_number(value, options=None):
        fmt = (options or {}).get('format')
        if not fmt:
            return _format_compact_number(value)
        try:
            number = float(value)
            if fmt in {'percent', 'percentage', '%'}:
                return f'{number:.1%}'
            if fmt == 'currency':
                return f'{number:,.2f}'
            return format(number, fmt)
        except (TypeError, ValueError):
            raise VisualBindingError('Unsupported numeric format')


    def _top_n_with_others(df, category_col, value_col, top_n=10, others_label='Others'):
        if df is None or df.empty or category_col not in df.columns or value_col not in df.columns:
            return pd.DataFrame(columns=[category_col, value_col])
        grouped = df.groupby(category_col, dropna=False)[value_col].sum().reset_index()
        grouped = grouped.sort_values(value_col, ascending=False)
        top_n = int(top_n or 10)
        if len(grouped) <= top_n:
            return grouped
        top = grouped.head(top_n).copy()
        other_value = grouped.iloc[top_n:][value_col].sum()
        other = pd.DataFrame([{category_col: others_label, value_col: other_value}])
        return pd.concat([top, other], ignore_index=True)


    def _save_chart_to_pdf(fig, pdf, x, y, w, h):
        _apply_chart_theme(fig)
        if len(fig.axes) == 1 and not fig.axes[0].tables and not getattr(fig, '_report_fitted', False):
            _fit_chart_layout(fig, fig.axes[0], w, h)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            try:
                fig.savefig(tmp.name, dpi=300)
                pdf.image(tmp.name, x=x, y=y, w=w, h=h)
            finally:
                plt.close(fig)
                os.unlink(tmp.name)


    def _wrapped_chart_title(title, w=None):
        clean = ' '.join(str(title or '').split())
        if not clean:
            return ''
        width = 42
        try:
            if w is not None:
                width = max(22, min(58, int(float(w) * 0.42)))
        except Exception:
            pass
        lines = textwrap.wrap(clean, width=width) or [clean]
        if len(lines) > 2:
            lines[1] = lines[1].rstrip(' .') + '…'
        return '\n'.join(lines[:2])


    def _set_axis_chart_title(ax, title, w=None):
        if not str(title or '').strip():
            return 0
        ax.set_title(
            str(title),
            loc='center',
            pad=CHART_TITLE_PAD,
            fontsize=CHART_TITLE_FONTSIZE,
            fontweight=CHART_TITLE_FONTWEIGHT,
            color=CHART_THEME.get('text_color', '#111827'),
        )
        return 1


    def _chart_top_margin(title_lines):
        return 0.88 if title_lines <= 1 else 0.80


    def _table_bbox_for_title(title_lines):
        if not title_lines:
            return [0.0, 0.04, 1.0, 0.92]
        return [0.0, 0.03, 1.0, 0.88 if title_lines <= 1 else 0.84]


    def _table_top_margin(title_lines):
        return 0.98


    def _draw_table_title(fig, title, w=None):
        if not str(title or '').strip():
            return 0
        renderer = fig.canvas.get_renderer()
        measure = _table_text_measurer(fig, renderer, CHART_TITLE_FONTSIZE, CHART_TITLE_FONTWEIGHT)
        wrapped, shortened = _wrap_table_text(title, fig.bbox.width * 0.94, measure, 2)
        if shortened:
            _COMPONENT_OUTCOME.setdefault('warnings', []).append('Table title was shortened with an ellipsis to fit the layout slot')
        line_count = wrapped.count('\n') + 1
        fig.text(
            0.5,
            0.965,
            wrapped,
            ha='center',
            va='top',
            fontsize=CHART_TITLE_FONTSIZE,
            fontweight=CHART_TITLE_FONTWEIGHT,
            color=CHART_THEME.get('text_color', '#111827'),
            usetex=False,
            parse_math=False,
        )
        return line_count


    def _table_text_measurer(fig, renderer, font_size, weight='normal'):
        from matplotlib.text import Text
        probe = Text(fontsize=font_size, fontweight=weight, usetex=False, parse_math=False)
        probe.set_figure(fig)
        cache = {}

        def measure(value):
            if value not in cache:
                probe.set_text(value)
                bounds = probe.get_window_extent(renderer)
                cache[value] = (bounds.width, bounds.height)
            return cache[value]
        return measure


    def _wrap_table_text(value, width, measure, max_lines=32):
        original = str(value)
        lines = []
        stopped = False

        def prefix_length(text, available):
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if measure(text[:middle])[0] <= available:
                    low = middle
                else:
                    high = middle - 1
            return low

        # Bound work for arbitrary uploaded strings; any omission is marked visibly.
        for paragraph in original[:4096].replace('\r\n', '\n').replace('\r', '\n').split('\n'):
            line = ''
            for word in paragraph.split():
                while word:
                    candidate = f'{line} {word}' if line else word
                    if measure(candidate)[0] <= width:
                        line = candidate
                        break
                    if line:
                        lines.append(line)
                        line = ''
                    else:
                        count = max(1, prefix_length(word, width))
                        lines.append(word[:count])
                        word = word[count:]
                    if len(lines) >= max_lines:
                        stopped = True
                        break
                if stopped:
                    break
            if stopped:
                break
            lines.append(line)
            if len(lines) > max_lines:
                stopped = True
                break
        shortened = stopped or len(original) > 4096
        lines = lines[:max_lines] or ['']
        if shortened:
            last = lines[-1].rstrip()
            lines[-1] = last[:prefix_length(last, max(0, width - measure('…')[0]))].rstrip() + '…'
        return '\n'.join(lines), shortened


    def _table_cell_layout(fig, renderer, grid, numeric, width, font_size, body_lines=32, header_lines=32):
        regular = _table_text_measurer(fig, renderer, font_size)
        bold = _table_text_measurer(fig, renderer, font_size, 'bold')
        padding = renderer.points_to_pixels(2)
        minimum, demand = [], []
        for column in range(len(grid[0])):
            measurements = [bold if row == 0 else regular for row in range(len(grid))]
            widths = [max((measure(line)[0] for line in grid[row][column][:4096].split('\n')), default=0)
                      for row, measure in enumerate(measurements)]
            number_width = max((widths[row] for row in range(1, len(grid)) if numeric[row][column]), default=0)
            minimum.append(max(renderer.points_to_pixels(12), number_width) + 2 * padding)
            demand.append(max(widths))
        minimum = np.array(minimum)
        if minimum.sum() > width:
            return None
        weights = np.sqrt(np.maximum(np.minimum(demand, width) - minimum, 1))
        widths = minimum + (width - minimum.sum()) * weights / weights.sum()
        wrapped, heights, shortened = [], [], False
        for row, values in enumerate(grid):
            measure = bold if row == 0 else regular
            cells = []
            for column, value in enumerate(values):
                text, omitted = _wrap_table_text(value, widths[column] - 2 * padding, measure, header_lines if row == 0 else body_lines)
                cells.append(text)
                shortened = shortened or omitted
            wrapped.append(cells)
            heights.append(max(measure('Ag')[1], *(measure(text)[1] for text in cells)) + 2 * padding)
        return {'text': wrapped, 'widths': widths, 'heights': heights, 'font_size': font_size,
                'shortened': shortened, 'padding': padding}


    def _render_table_image(pdf, dataframe, x, y, w, h, title=None, theme=None, options=None):
        options = options or {}
        theme = theme or CHART_THEME
        table_theme = dict(theme.get('table', {}))
        if isinstance(options.get('table_theme'), dict):
            table_theme.update(options.get('table_theme'))
        df = dataframe.copy() if dataframe is not None else pd.DataFrame()
        max_rows = 5 if h < 60 else 8 if h < 95 else 12
        max_cols = 4 if w < 100 else 6 if w < 150 else 8
        if len(df) > max_rows or len(df.columns) > max_cols:
            _COMPONENT_OUTCOME.setdefault('warnings', []).append('Table rows or columns were limited to fit the layout slot')
        if options.get('show_totals') and len(df) > max_rows:
            df = pd.concat([df.head(max_rows - 1), df.tail(1)])
        df = df.iloc[:max_rows, :max_cols]
        numeric = [[False] * len(df.columns)]
        grid = [[str(column) for column in df.columns]]
        for values in df.itertuples(index=False, name=None):
            missing = [bool(pd.isna(value)) if pd.api.types.is_scalar(value) else False for value in values]
            numbers = [isinstance(value, (int, float, np.integer, np.floating)) and not absent for value, absent in zip(values, missing)]
            grid.append([_format_visual_number(value, options) if number else '' if absent else str(value)
                         for value, number, absent in zip(values, numbers, missing)])
            numeric.append(numbers)
        fig, ax = plt.subplots(figsize=(w / 25.4, h / 25.4), dpi=300)
        ax.axis('off')
        fig.subplots_adjust(left=0.02, right=0.98, top=_table_top_margin(0), bottom=0.02)
        title_lines = _draw_table_title(fig, title, w)
        if df.empty:
            _COMPONENT_OUTCOME.update(status='empty', message='No rows available for the table')
            ax.text(0.5, 0.5, 'Dados indisponiveis', ha='center', va='center', fontsize=CHART_LABEL_FONTSIZE)
            _save_chart_to_pdf(fig, pdf, x, y, w, h)
            return
        table_bbox = _table_bbox_for_title(title_lines)
        renderer = fig.canvas.get_renderer()
        axes_bounds = ax.get_window_extent(renderer)
        if title_lines:
            title_bottom = fig.texts[-1].get_window_extent(renderer).y0 - renderer.points_to_pixels(3)
            table_bbox[3] = max(0, (title_bottom - axes_bounds.y0) / axes_bounds.height - table_bbox[1])
        width = axes_bounds.width * table_bbox[2]
        height = axes_bounds.height * table_bbox[3]
        min_font = 4.5
        # Numeric values stay intact; if necessary reduce visible columns instead of cutting digits.
        while len(grid[0]) > 1 and _table_cell_layout(fig, renderer, grid, numeric, width, min_font) is None:
            grid = [row[:-1] for row in grid]
            numeric = [row[:-1] for row in numeric]
        layout = None
        for font_size in np.arange(max(min_font, CHART_TABLE_FONTSIZE), min_font - 0.01, -0.5):
            layout = _table_cell_layout(fig, renderer, grid, numeric, width, font_size)
            if layout and sum(layout['heights']) <= height:
                break
        else:
            for body_lines, header_lines in ((3, 3), (2, 3), (1, 3), (1, 2), (1, 1)):
                layout = _table_cell_layout(fig, renderer, grid, numeric, width, min_font, body_lines, header_lines)
                if layout and sum(layout['heights']) <= height:
                    break
            else:
                while len(grid) > (3 if options.get('show_totals') else 2):
                    index = -2 if options.get('show_totals') else -1
                    grid.pop(index)
                    numeric.pop(index)
                    layout = _table_cell_layout(fig, renderer, grid, numeric, width, min_font, 1, 1)
                    if layout and sum(layout['heights']) <= height:
                        break
        if not layout or sum(layout['heights']) > height:
            _COMPONENT_OUTCOME.update(status='empty', code='layout_too_small', message='Table slot is too small; enlarge it or select fewer columns')
            ax.text(0.5, 0.4, 'Amplie a tabela', ha='center', va='center', fontsize=CHART_LABEL_FONTSIZE, clip_on=True)
            _save_chart_to_pdf(fig, pdf, x, y, w, h)
            return
        if len(grid) - 1 < len(df) or len(grid[0]) < len(df.columns):
            warning = 'Table rows or columns were limited to fit the layout slot'
            if warning not in _COMPONENT_OUTCOME.setdefault('warnings', []):
                _COMPONENT_OUTCOME['warnings'].append(warning)
        if layout['shortened']:
            _COMPONENT_OUTCOME.setdefault('warnings', []).append('Table text was shortened with ellipses to fit the layout slot')
        table = ax.table(
            cellText=layout['text'][1:],
            colLabels=layout['text'][0],
            colWidths=layout['widths'] / axes_bounds.width,
            cellLoc='center',
            loc='upper center',
            bbox=table_bbox,
        )
        table.auto_set_font_size(False)
        table.set_fontsize(layout['font_size'])
        for (row, col), cell in table.get_celld().items():
            cell.set_height(layout['heights'][row] / axes_bounds.height)
            cell.PAD = layout['padding'] / layout['widths'][col]
            cell.get_text().set_usetex(False)
            cell.get_text().set_parse_math(False)
            cell.get_text().set_fontweight('bold' if row == 0 else 'normal')
            # Matplotlib finalizes cell geometry during drawing. A clip path made
            # from the earlier cell rectangle can hide the fitted text entirely.
            cell.set_edgecolor(table_theme.get('border', '#d1d5db'))
            cell.set_linewidth(0.4)
            if row == 0:
                cell.set_facecolor(table_theme.get('header_bg', '#111827'))
                cell.get_text().set_color(table_theme.get('header_text', '#ffffff'))
                cell.get_text().set_fontweight('bold')
            else:
                cell.set_facecolor(table_theme.get('alt_row_bg', '#f9fafb') if options.get('striped_rows', True) and row % 2 == 0 else table_theme.get('row_bg', '#ffffff'))
                cell.get_text().set_color(theme.get('text_color', '#111827'))
        _save_chart_to_pdf(fig, pdf, x, y, w, h)


    def _install_standard_chart_style():
        global _STANDARD_CHART_STYLE_INSTALLED
        from matplotlib.axes import Axes
        if getattr(Axes.set_title, '_pdf_report_style_owner', None) is _STANDARD_CHART_STYLE_TOKEN:
            return
        original_set_title = getattr(Axes.set_title, '_pdf_report_original', Axes.set_title)
        original_set_xlabel = getattr(Axes.set_xlabel, '_pdf_report_original', Axes.set_xlabel)
        original_set_ylabel = getattr(Axes.set_ylabel, '_pdf_report_original', Axes.set_ylabel)
        original_tick_params = getattr(Axes.tick_params, '_pdf_report_original', Axes.tick_params)
        original_legend = getattr(Axes.legend, '_pdf_report_original', Axes.legend)
        original_plt_legend = getattr(plt.legend, '_pdf_report_original', plt.legend)

        def set_title(self, label, fontdict=None, loc=None, pad=None, *, y=None, **kwargs):
            fontdict = dict(fontdict or {})
            fontdict['fontsize'] = CHART_TITLE_FONTSIZE
            fontdict['fontweight'] = CHART_TITLE_FONTWEIGHT
            kwargs['fontsize'] = CHART_TITLE_FONTSIZE
            kwargs['fontweight'] = CHART_TITLE_FONTWEIGHT
            return original_set_title(self, label, fontdict=fontdict, loc=loc, pad=CHART_TITLE_PAD if pad is None else pad, y=y, **kwargs)

        def set_xlabel(self, xlabel, fontdict=None, labelpad=None, loc=None, **kwargs):
            fontdict = dict(fontdict or {})
            fontdict['fontsize'] = CHART_LABEL_FONTSIZE
            fontdict['fontweight'] = 'normal'
            kwargs['fontsize'] = CHART_LABEL_FONTSIZE
            kwargs['fontweight'] = 'normal'
            return original_set_xlabel(self, xlabel, fontdict=fontdict, labelpad=labelpad, loc=loc, **kwargs)

        def set_ylabel(self, ylabel, fontdict=None, labelpad=None, loc=None, **kwargs):
            fontdict = dict(fontdict or {})
            fontdict['fontsize'] = CHART_LABEL_FONTSIZE
            fontdict['fontweight'] = 'normal'
            kwargs['fontsize'] = CHART_LABEL_FONTSIZE
            kwargs['fontweight'] = 'normal'
            return original_set_ylabel(self, ylabel, fontdict=fontdict, labelpad=labelpad, loc=loc, **kwargs)

        def tick_params(self, axis='both', **kwargs):
            kwargs['labelsize'] = 5 if CHART_STYLE.get('id') == 'dense_dashboard' else CHART_TICK_FONTSIZE
            return original_tick_params(self, axis=axis, **kwargs)

        def legend(self, *args, **kwargs):
            kwargs['fontsize'] = 5 if CHART_STYLE.get('id') == 'dense_dashboard' else CHART_LEGEND_FONTSIZE
            return original_legend(self, *args, **kwargs)

        def plt_legend(*args, **kwargs):
            kwargs['fontsize'] = CHART_LEGEND_FONTSIZE
            return original_plt_legend(*args, **kwargs)

        for wrapper, original in ((set_title, original_set_title), (set_xlabel, original_set_xlabel),
                                  (set_ylabel, original_set_ylabel), (tick_params, original_tick_params),
                                  (legend, original_legend), (plt_legend, original_plt_legend)):
            wrapper._pdf_report_original = original
            wrapper._pdf_report_style_owner = _STANDARD_CHART_STYLE_TOKEN
        Axes.set_title = set_title
        Axes.set_xlabel = set_xlabel
        Axes.set_ylabel = set_ylabel
        Axes.tick_params = tick_params
        Axes.legend = legend
        plt.legend = plt_legend
        _STANDARD_CHART_STYLE_INSTALLED = True
    """).strip() + "\n\n" + fitting_helpers + "\n\n" + library_renderer_source() + "\n"


def library_renderer_source() -> str:
    return dedent(r"""
    def _library_first_dataframe(dataframes, source=None):
        if not dataframes:
            return pd.DataFrame()
        if source and source in dataframes:
            return dataframes[source].copy()
        if source:
            source_base = os.path.basename(str(source))
            for key, dataframe in dataframes.items():
                if os.path.basename(str(key)) == source_base:
                    return dataframe.copy()
            raise VisualBindingError(f'Data source not found: {source}')
        first = next(iter(dataframes.values()))
        return first.copy() if first is not None else pd.DataFrame()

    def _library_role(binding, role_name, default=None):
        roles = (binding or {}).get('roles') or {}
        return roles.get(role_name, default)

    def _library_role_list(binding, role_name):
        value = _library_role(binding, role_name, [])
        if isinstance(value, list):
            return value
        if value in (None, ''):
            return []
        return [value]

    def _library_column_name(value):
        text = str(value or '').strip()
        if ':' in text:
            text = text.rsplit(':', 1)[1]
        return text

    def _library_label_map(options=None):
        options = options or {}
        labels = options.get('labels') or options.get('label_map') or options.get('axis_labels') or {}
        return labels if isinstance(labels, dict) else {}

    def _library_humanize_label(value):
        raw = _library_column_name(value)
        text = str(raw or '').strip()
        if not text:
            return ''
        known = {
            'base_stat_total': 'Total de Stats Base',
            'base_experience': 'Experiencia Base',
            'capture_rate': 'Taxa de Captura',
            'is_legendary': 'Lendario',
            'is_mythical': 'Mitico',
            'is_baby': 'Bebe',
            'type_1': 'Tipo Primario',
            'type_2': 'Tipo Secundario',
            'generation': 'Geracao',
            'name': 'Nome',
            'pokedex_number': 'Numero na Pokedex',
            'attack': 'Ataque',
            'defense': 'Defesa',
            'hp': 'HP',
            'speed': 'Velocidade',
            'sp_attack': 'Ataque Especial',
            'sp_defense': 'Defesa Especial',
            'height_m': 'Altura',
            'weight_kg': 'Peso',
            'revenue': 'Receita',
            'sales': 'Vendas',
            'region': 'Regiao',
            'customer': 'Cliente',
            'date': 'Data',
            'price': 'Preco',
            'quantity': 'Quantidade',
            'count': 'Contagem',
        }
        normalized = text.strip().lower()
        if normalized in known:
            return known[normalized]
        words = [word for word in re.split(r'[_\s\-]+', text) if word]
        replacements = {
            'avg': 'Media',
            'mean': 'Media',
            'total': 'Total',
            'base': 'Base',
            'stat': 'Stat',
            'stats': 'Stats',
            'rate': 'Taxa',
            'count': 'Contagem',
            'id': 'ID',
            'number': 'Numero',
            'type': 'Tipo',
            'generation': 'Geracao',
            'name': 'Nome',
            'date': 'Data',
            'value': 'Valor',
        }
        return ' '.join(replacements.get(word.lower(), word.capitalize()) for word in words)

    def _library_display_label(value, options=None):
        raw = _library_column_name(value)
        labels = _library_label_map(options)
        for key in (value, raw, str(value or ''), str(raw or '')):
            if key in labels and str(labels[key]).strip():
                return str(labels[key]).strip()
        return _library_humanize_label(raw)

    def _library_display_dataframe(df, options=None):
        if df is None or df.empty:
            return df
        renamed = {}
        used = set()
        for column in df.columns:
            label = _library_display_label(column, options)
            candidate = label
            index = 2
            while candidate in used:
                candidate = f'{label} {index}'
                index += 1
            used.add(candidate)
            renamed[column] = candidate
        return df.rename(columns=renamed)

    def _library_measure_label(value, options=None, aggregation=None):
        if str(aggregation or '').lower() == 'count':
            labels = _library_label_map(options)
            raw = _library_column_name(value)
            return str(labels.get(value) or labels.get(raw) or labels.get('count') or 'Contagem')
        return _library_display_label(value, options)

    def _library_resolve_column(df, value):
        column = _library_column_name(value)
        if not column:
            return None
        if column in df.columns:
            return column
        lookup = {str(existing).strip().casefold(): existing for existing in df.columns}
        return lookup.get(column.casefold())

    def _library_resolve_columns(df, values):
        resolved = []
        for value in values or []:
            column = _library_resolve_column(df, value)
            if column and column not in resolved:
                resolved.append(column)
        return resolved

    def _library_apply_filters(df, filters):
        result = df.copy()
        for item in filters or []:
            if not isinstance(item, dict):
                raise VisualBindingError('Filters must be objects with a column and operator')
            column = _library_resolve_column(result, item.get('column'))
            if not column:
                raise VisualBindingError(f"Filter column not found: {item.get('column')}")
            op = str(item.get('operator') or item.get('op') or '==').lower()
            value = item.get('value')
            try:
                if op in {'==', 'eq', '='}:
                    result = result[result[column] == value]
                elif op in {'!=', 'ne'}:
                    result = result[result[column] != value]
                elif op in {'>', 'gt'}:
                    result = result[pd.to_numeric(result[column], errors='coerce') > float(value)]
                elif op in {'>=', 'gte'}:
                    result = result[pd.to_numeric(result[column], errors='coerce') >= float(value)]
                elif op in {'<', 'lt'}:
                    result = result[pd.to_numeric(result[column], errors='coerce') < float(value)]
                elif op in {'<=', 'lte'}:
                    result = result[pd.to_numeric(result[column], errors='coerce') <= float(value)]
                elif op in {'in', 'isin'} and isinstance(value, (list, tuple, set)):
                    result = result[result[column].isin(value)]
                elif op in {'contains'}:
                    result = result[result[column].astype('str').str.contains(str(value), case=False, na=False, regex=False)]
                else:
                    raise ValueError(f'Unsupported filter operator or value: {op}')
            except (TypeError, ValueError) as exc:
                raise VisualBindingError(f'Could not apply filter on {column}: {exc}') from exc
        return result

    def _library_dataframe(dataframes, binding):
        binding = binding or {}
        df = _library_first_dataframe(dataframes, binding.get('source'))
        result = _library_apply_filters(df, binding.get('filters'))
        _COMPONENT_OUTCOME.update(input_rows=len(df), filtered_rows=len(result))
        return result

    def _library_empty_chart(pdf, x, y, w, h, title=None, message='Dados indisponiveis'):
        _COMPONENT_OUTCOME.update(status='empty', message=str(message))
        fig, ax = plt.subplots(figsize=(max(w, 1) / 25.4, max(h, 1) / 25.4))
        ax.axis('off')
        title_lines = _set_axis_chart_title(ax, title, w)
        ax.text(0.5, 0.5, str(message), ha='center', va='center', fontsize=CHART_LABEL_FONTSIZE, color=CHART_THEME.get('muted_text_color', '#6b7280'))
        if title_lines:
            fig.subplots_adjust(top=_chart_top_margin(title_lines))
        _save_chart_to_pdf(fig, pdf, x, y, w, h)

    def _library_numeric(df, column):
        return pd.to_numeric(df[column], errors='coerce')

    def _library_aggregate(df, category_cols, value_col, aggregation='sum'):
        category_cols = [col for col in category_cols if col in df.columns]
        if not category_cols or value_col not in df.columns:
            return pd.DataFrame(columns=category_cols + [value_col])
        work = df[category_cols + [value_col]].copy()
        aggregation = str(aggregation or 'sum').lower()
        if aggregation not in {'sum', 'mean', 'median', 'min', 'max', 'count'}:
            raise VisualBindingError(f'Unsupported aggregation: {aggregation}')
        if aggregation != 'count':
            work[value_col] = pd.to_numeric(work[value_col], errors='coerce')
        work = work.dropna(subset=[value_col])
        if work.empty:
            return pd.DataFrame(columns=category_cols + [value_col])
        grouped = work.groupby(category_cols, dropna=False, sort=False)[value_col].agg(aggregation).reset_index()
        return grouped

    def _library_limit_top(df, value_col, top_n=None, aggregate_others=False, category_col=None, sort='desc'):
        if df.empty or value_col not in df.columns:
            return df
        result = df.sort_values(value_col, ascending=sort == 'asc') if sort != 'none' else df
        if not top_n:
            return result
        top_n = max(1, int(top_n))
        if len(result) <= top_n:
            return result
        if aggregate_others and category_col and category_col in result.columns:
            top = result.head(top_n).copy()
            others = result.iloc[top_n:].copy()
            other_row = {column: '' for column in result.columns}
            other_row[category_col] = 'Outros'
            other_row[value_col] = others[value_col].sum()
            return pd.concat([top, pd.DataFrame([other_row])], ignore_index=True)
        return result.head(top_n)

    def _line_date_granularity(options=None, title=None, date_label=None):
        options = options or {}
        configured = str(options.get('date_granularity') or options.get('granularity') or '').strip().lower()
        if configured in {'week', 'quarter', 'year'}:
            return configured
        if configured in {'day', 'daily', 'dia', 'diario', 'diaria'}:
            return 'day'
        if configured in {'month', 'monthly', 'mes', 'mensal'}:
            return 'month'
        context = f"{title or ''} {date_label or ''}".lower()
        if any(token in context for token in ('mensal', 'mes ', 'mês ', 'month', 'monthly')):
            return 'month'
        return 'day'

    def render_bar_chart(pdf, dataframes, x, y, w, h, *, category, value, source=None, series=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        category_col = _library_resolve_column(df, category)
        value_col = _library_resolve_column(df, value)
        series_col = _library_resolve_column(df, series) if series else None
        if df.empty or not category_col or not value_col:
            _library_empty_chart(pdf, x, y, w, h, title)
            return

        top_n = options.get('top_n') or options.get('limit')
        aggregation = options.get('aggregation') or 'sum'
        measure_label = _library_measure_label(value_col, options, aggregation)
        orientation = str(options.get('orientation') or 'horizontal').lower()
        show_values = bool(options.get('show_values'))
        colors = _chart_colors(12)
        single_color = options.get('bar_color') or options.get('color')
        fig, ax = plt.subplots(figsize=(max(w, 1) / 25.4, max(h, 1) / 25.4))

        if series_col:
            grouped = _library_aggregate(df, [category_col, series_col], value_col, aggregation)
            if grouped.empty:
                _library_empty_chart(pdf, x, y, w, h, title)
                return
            totals = grouped.groupby(category_col)[value_col].sum()
            if options.get('sort') != 'none':
                totals = totals.sort_values(ascending=options.get('sort') == 'asc')
            categories = list(totals.index[: int(top_n or 8)])
            selected = grouped[grouped[category_col].isin(categories)]
            if options.get('aggregate_others') and aggregation in {'sum', 'count'} and len(totals) > len(categories):
                remaining = grouped[~grouped[category_col].isin(categories)].groupby(series_col, dropna=False)[value_col].sum().reset_index()
                others_label = 'Outros'
                while others_label in totals.index:
                    others_label += ' (agrupados)'
                remaining[category_col] = others_label
                selected = pd.concat([selected, remaining], ignore_index=True)
                categories.append(others_label)
            grouped = selected
            pivot = grouped.pivot_table(index=category_col, columns=series_col, values=value_col, aggfunc='sum', fill_value=0)
            pivot = pivot.reindex(categories)
            pivot.plot(kind='barh' if orientation == 'horizontal' else 'bar', ax=ax, edgecolor='none', linewidth=0,
                       color=[single_color] * len(pivot.columns) if single_color else _chart_colors(len(pivot.columns)))
            if orientation == 'horizontal':
                ax.invert_yaxis()
            if show_values:
                for container in ax.containers:
                    ax.bar_label(container, labels=[_format_visual_number(v, options) for v in container.datavalues], padding=2, fontsize=CHART_LABEL_FONTSIZE)
            if _monochrome_charts():
                hatches = ('', '///', 'xxx', '...', '\\\\', '++')
                for index, container in enumerate(ax.containers):
                    for bar in container:
                        bar.set_hatch(hatches[index % len(hatches)])
                        bar.set_edgecolor('#111111')
                        bar.set_linewidth(0.3)
            category_label = _library_display_label(category_col, options)
            ax.set_xlabel(measure_label if orientation == 'horizontal' else category_label)
            ax.set_ylabel(category_label if orientation == 'horizontal' and w >= 100 else measure_label if orientation != 'horizontal' else '')
            ax.legend(title=options.get('legend_title') or _library_display_label(series_col, options), loc='upper left', bbox_to_anchor=(1.01, 1.0), frameon=False)
            ax.tick_params(axis='x', rotation=0)
        else:
            grouped = _library_aggregate(df, [category_col], value_col, aggregation)
            grouped = _library_limit_top(grouped, value_col, top_n, bool(options.get('aggregate_others')) and aggregation in {'sum', 'count'}, category_col, options.get('sort', 'desc'))
            if grouped.empty:
                _library_empty_chart(pdf, x, y, w, h, title)
                return
            labels = [str(item) for item in grouped[category_col].fillna('')]
            values = grouped[value_col].fillna(0).tolist()
            positions = np.arange(len(labels))
            if orientation == 'vertical':
                bars = ax.bar(positions, values, color=single_color or colors[: len(values)], edgecolor='none', linewidth=0)
                ax.set_xticks(positions, labels)
                ax.set_ylabel(measure_label)
                if show_values:
                    ax.bar_label(bars, labels=[_format_visual_number(v, options) for v in values], padding=2, fontsize=CHART_LABEL_FONTSIZE)
            else:
                bars = ax.barh(positions, values, color=single_color or colors[: len(values)], edgecolor='none', linewidth=0)
                ax.set_yticks(positions, labels)
                ax.invert_yaxis()
                ax.set_xlabel(measure_label)
                if show_values:
                    ax.bar_label(bars, labels=[_format_visual_number(v, options) for v in values], padding=2, fontsize=CHART_LABEL_FONTSIZE)

        if options.get('format'):
            axis = ax.yaxis if orientation == 'vertical' else ax.xaxis
            axis.set_major_formatter(mpl.ticker.FuncFormatter(lambda value, _pos: _format_visual_number(value, options)))

        ax.margins(**({'y': 0.2} if orientation == 'vertical' else {'x': 0.2}))
        _set_axis_chart_title(ax, title, w)
        _fit_chart_layout(fig, ax, w, h, category_axis='x' if orientation == 'vertical' else 'y',
                          category_labels=[str(item) for item in pivot.index] if series_col else labels)
        _save_chart_to_pdf(fig, pdf, x, y, w, h)

    def render_line_chart(pdf, dataframes, x, y, w, h, *, date, value, source=None, series=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        date_col = _library_resolve_column(df, date)
        value_col = _library_resolve_column(df, value)
        series_col = _library_resolve_column(df, series) if series else None
        if df.empty or not date_col or not value_col:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        aggregation = str(options.get('aggregation') or 'sum').lower()
        if aggregation not in {'sum', 'mean', 'median', 'min', 'max', 'count'}:
            raise VisualBindingError(f'Unsupported aggregation: {aggregation}')
        date_label = _library_display_label(date_col, options)
        date_name = _library_column_name(date_col).strip().lower()
        work = df[[date_col, value_col] + ([series_col] if series_col else [])].copy()
        numeric_date = pd.to_numeric(work[date_col], errors='coerce')
        is_year_axis = (
            date_name in {'year', 'ano', 'año'}
            and numeric_date.notna().any()
            and numeric_date.dropna().between(1000, 3000).all()
            and (numeric_date.dropna() % 1 == 0).all()
        )
        if is_year_axis:
            work[date_col] = numeric_date.astype('Int64')
        else:
            work[date_col] = pd.to_datetime(work[date_col], errors='coerce')
        date_granularity = _line_date_granularity(options, title, date_label)
        if not is_year_axis:
            frequency = {'day': 'D', 'week': 'W', 'month': 'M', 'quarter': 'Q', 'year': 'Y'}[date_granularity]
            work[date_col] = work[date_col].dt.to_period(frequency).dt.to_timestamp()
        if aggregation != 'count':
            work[value_col] = pd.to_numeric(work[value_col], errors='coerce')
            work = work.dropna(subset=[date_col, value_col])
        else:
            work = work.dropna(subset=[date_col, value_col])
        if work.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        fig, ax = plt.subplots(figsize=(max(w, 1) / 25.4, max(h, 1) / 25.4))
        marker = 'o' if options.get('show_markers', True) else None
        if series_col:
            grouped = work.groupby([date_col, series_col], dropna=False)[value_col].agg(aggregation).reset_index()
            pivot = grouped.pivot_table(index=date_col, columns=series_col, values=value_col, aggfunc='sum').sort_index()
            if options.get('rolling_average'):
                pivot = pivot.rolling(int(options.get('rolling_window') or 3), min_periods=1).mean()
            if options.get('sort') == 'desc':
                pivot = pivot.sort_index(ascending=False)
            if options.get('limit'):
                pivot = pivot.head(int(options['limit']))
            line_color = options.get('line_color') or options.get('color')
            pivot.plot(ax=ax, marker=marker, linewidth=1.8, color=[line_color] * len(pivot.columns) if line_color else _chart_colors(len(pivot.columns)))
            if _monochrome_charts():
                for index, line in enumerate(ax.lines):
                    line.set_linestyle(('-', '--', '-.', ':')[index % 4])
                    line.set_marker(('o', 's', '^', 'D', 'v', 'P')[index % 6])
            ax.legend(title=options.get('legend_title') or _library_display_label(series_col, options), loc='upper left', bbox_to_anchor=(1.01, 1.0), frameon=False)
        else:
            grouped = work.groupby(date_col, dropna=False)[value_col].agg(aggregation).reset_index().sort_values(date_col)
            if options.get('rolling_average'):
                grouped[value_col] = grouped[value_col].rolling(int(options.get('rolling_window') or 3), min_periods=1).mean()
            if options.get('sort') == 'desc':
                grouped = grouped.sort_values(date_col, ascending=False)
            if options.get('limit'):
                grouped = grouped.head(int(options['limit']))
            line_color = options.get('line_color') or options.get('color') or _chart_colors(1)[0]
            ax.plot(grouped[date_col], grouped[value_col], marker=marker, linewidth=1.9, color=line_color)
        if options.get('show_values'):
            for line in ax.lines:
                for point_x, point_y in zip(line.get_xdata(), line.get_ydata()):
                    if np.ma.is_masked(point_y) or pd.isna(point_y):
                        continue
                    ax.annotate(_format_visual_number(point_y, options), (point_x, point_y), xytext=(0, 4), textcoords='offset points', ha='center', fontsize=CHART_LABEL_FONTSIZE)
        title_lines = _set_axis_chart_title(ax, title, w)
        if is_year_axis:
            year_ticks = sorted(grouped[date_col].dropna().astype(int).unique().tolist())
            if len(year_ticks) <= 12:
                ax.set_xticks(year_ticks)
            ax.xaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda x, _pos: f'{int(x)}' if float(x).is_integer() else ''))
        ax.set_xlabel(date_label if not options.get('hide_x_label') and not (series_col and h < 65) else '')
        ax.set_ylabel(_library_measure_label(value_col, options, aggregation))
        if options.get('format'):
            ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda value, _pos: _format_visual_number(value, options)))
        ax.tick_params(axis='x', rotation=0)
        ax.margins(y=0.2)
        _fit_chart_layout(fig, ax, w, h)
        _save_chart_to_pdf(fig, pdf, x, y, w, h)

    def render_scatter_plot(pdf, dataframes, x, y, w, h, *, x_value, y_value, source=None, color=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        x_col = _library_resolve_column(df, x_value)
        y_col = _library_resolve_column(df, y_value)
        color_col = _library_resolve_column(df, color) if color else None
        if df.empty or not x_col or not y_col:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        work = df[[x_col, y_col] + ([color_col] if color_col else [])].copy()
        work[x_col] = pd.to_numeric(work[x_col], errors='coerce')
        work[y_col] = pd.to_numeric(work[y_col], errors='coerce')
        work = work.dropna(subset=[x_col, y_col])
        max_points = int(options.get('max_points') or 250)
        if options.get('limit'):
            max_points = min(max_points, int(options['limit']))
        valid_rows = len(work)
        if len(work) > max_points:
            work = work.sample(max_points, random_state=7)
            _COMPONENT_OUTCOME.setdefault('warnings', []).append('Scatter points were sampled deterministically')
        _COMPONENT_OUTCOME.update(valid_rows=valid_rows, plotted_rows=len(work))
        if work.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        fig, ax = plt.subplots(figsize=(max(w, 1) / 25.4, max(h, 1) / 25.4))
        if color_col:
            work[color_col] = work[color_col].fillna('Sem categoria').astype(str)
            categories = list(work[color_col].unique())
            if len(categories) > 8:
                major = work[color_col].value_counts().head(7).index
                others_label = 'Outros'
                while others_label in categories:
                    others_label += ' (agrupados)'
                work[color_col] = work[color_col].where(work[color_col].isin(major), others_label)
                categories = list(work[color_col].unique())
                _COMPONENT_OUTCOME.setdefault('warnings', []).append('Minor scatter categories were grouped as Others; all sampled points are shown')
            colors = _chart_colors(len(categories))
            for index, category in enumerate(categories):
                subset = work[work[color_col].astype(str) == category]
                marker = ('o', '^', 's', 'D', 'v', 'P', 'X', '*')[index % 8] if _monochrome_charts() else 'o'
                ax.scatter(subset[x_col], subset[y_col], s=24, alpha=0.85 if _monochrome_charts() else 0.72,
                           color=colors[index], marker=marker,
                           edgecolors='#111111' if _monochrome_charts() else 'none',
                           linewidths=0.4, label=str(category))
            ax.legend(title=options.get('legend_title'), loc='upper left', bbox_to_anchor=(1.01, 1.0), frameon=False)
        else:
            ax.scatter(work[x_col], work[y_col], s=24, alpha=0.72, color=options.get('color') or _chart_colors(1)[0])
        if options.get('show_labels'):
            for index, row in work.iterrows():
                ax.annotate(str(index), (row[x_col], row[y_col]), fontsize=CHART_TICK_FONTSIZE)
        if options.get('show_trendline', True) and len(work) >= 3:
            try:
                coeffs = np.polyfit(work[x_col], work[y_col], 1)
                xs = np.array([work[x_col].min(), work[x_col].max()])
                ax.plot(xs, coeffs[0] * xs + coeffs[1], color='#444444' if _monochrome_charts() else _chart_colors(2)[1],
                        linestyle='--' if _monochrome_charts() else '-', linewidth=1.3)
            except Exception:
                pass
        title_lines = _set_axis_chart_title(ax, title, w)
        ax.set_xlabel(_library_display_label(x_col, options))
        ax.set_ylabel(_library_display_label(y_col, options))
        if options.get('format'):
            ax.xaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda value, _pos: _format_visual_number(value, options)))
            ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda value, _pos: _format_visual_number(value, options)))
        _fit_chart_layout(fig, ax, w, h)
        _save_chart_to_pdf(fig, pdf, x, y, w, h)

    def render_donut_chart(pdf, dataframes, x, y, w, h, *, category, value, source=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        category_col = _library_resolve_column(df, category)
        value_col = _library_resolve_column(df, value)
        if df.empty or not category_col or not value_col:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        aggregation = options.get('aggregation') or 'sum'
        if aggregation not in {'sum', 'count'}:
            raise VisualBindingError('Part-to-whole charts require an additive aggregation (sum or count)')
        grouped = _library_aggregate(df, [category_col], value_col, aggregation)
        if not grouped.empty and (grouped[value_col] < 0).any():
            raise ValueError('Part-to-whole charts require non-negative values')
        top_n = options.get('top_n') or 5
        if options.get('limit') and options.get('aggregate_others') and len(grouped) > top_n:
            top_n = max(0, top_n - 1)
            if top_n == 0:
                grouped = pd.DataFrame([{category_col: 'Outros', value_col: grouped[value_col].sum()}])
        grouped = _library_limit_top(grouped, value_col, top_n, bool(options.get('aggregate_others')) and aggregation in {'sum', 'count'}, category_col, options.get('sort', 'desc'))
        grouped = grouped[grouped[value_col] > 0]
        if grouped.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        labels = [str(item) for item in grouped[category_col].fillna('')]
        values = grouped[value_col].tolist()
        fig, ax = plt.subplots(figsize=(max(w, 1) / 25.4, max(h, 1) / 25.4))
        wedges, _ = ax.pie(values, colors=_chart_colors(len(values)), startangle=90, wedgeprops={'width': 0.42, 'edgecolor': 'white'})
        if options.get('show_percent', True):
            labels = [f'{label} · {number / sum(values):.1%}' for label, number in zip(labels, values)]
        ax.legend(wedges, labels, frameon=False)
        center = ax.text(0, 0, _format_visual_number(sum(values), options), ha='center', va='center', fontsize=CHART_TITLE_FONTSIZE, fontweight=CHART_TITLE_FONTWEIGHT)
        title_lines = _set_axis_chart_title(ax, title, w)
        ax.axis('off')
        ax.set_aspect('equal', adjustable='box')
        if _fit_chart_layout(fig, ax, w, h):
            _fit_chart_value(fig, ax, center, 0.43, 0.3)
        _save_chart_to_pdf(fig, pdf, x, y, w, h)

    def render_gauge_chart(pdf, dataframes, x, y, w, h, *, value, source=None, target=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        value_col = _library_resolve_column(df, value)
        target_col = _library_resolve_column(df, target) if target else None
        if df.empty or not value_col:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        aggregation = str(options.get('aggregation') or 'sum').lower()
        values = df[value_col].dropna() if aggregation == 'count' else pd.to_numeric(df[value_col], errors='coerce').dropna()
        if values.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        if aggregation not in {'sum', 'mean', 'median', 'min', 'max', 'count'}:
            raise ValueError(f'Unsupported gauge aggregation: {aggregation}')
        actual = values.agg(aggregation)
        if target_col:
            maximum = pd.to_numeric(df[target_col], errors='coerce').sum(min_count=1)
        else:
            maximum = options.get('target') or options.get('max_value')
        try:
            maximum = float(maximum)
        except (TypeError, ValueError):
            maximum = float('nan')
        if not np.isfinite(maximum) or maximum <= 0 or not np.isfinite(actual):
            _library_empty_chart(pdf, x, y, w, h, title, message='Meta ou valor indisponivel')
            return
        ratio = float(actual) / maximum
        from matplotlib.patches import Wedge
        fig, ax = plt.subplots(figsize=(max(w, 1) / 25.4, max(h, 1) / 25.4))
        ax.set_xlim(-1.2, 1.2)
        ax.set_ylim(-0.2, 1.2)
        ax.axis('off')
        ax.set_aspect('equal', adjustable='box')
        ax.add_patch(Wedge((0, 0), 1, 0, 180, width=0.28, facecolor=CHART_THEME.get('grid_color', '#e5e7eb'), edgecolor='white'))
        ax.add_patch(Wedge((0, 0), 1, 0, 180 * max(0, min(1, ratio)), width=0.28, facecolor=_chart_colors(1)[0], edgecolor='white'))
        label = f"{ratio * 100:.0f}%" if options.get('show_center_value', True) else _format_visual_number(actual, options)
        center = ax.text(0, 0.2, label, ha='center', va='center', fontsize=max(11, CHART_TITLE_FONTSIZE + 4), fontweight='bold')
        actual_label = _format_visual_number(actual, options)
        footer = ax.text(0, -0.04, actual_label, ha='center', va='center', fontsize=CHART_LABEL_FONTSIZE, color=CHART_THEME.get('muted_text_color', '#6b7280')) if label != actual_label else None
        title_lines = _set_axis_chart_title(ax, title, w)
        if _fit_chart_layout(fig, ax, w, h):
            if _fit_chart_value(fig, ax, center) and footer is not None:
                _fit_chart_value(fig, ax, footer, height_fraction=0.16)
        _save_chart_to_pdf(fig, pdf, x, y, w, h)

    def render_summary_table(pdf, dataframes, x, y, w, h, *, dimensions, metrics, source=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        dimension_cols = _library_resolve_columns(df, dimensions)
        metric_cols = _library_resolve_columns(df, metrics)
        if df.empty or not dimension_cols or not metric_cols:
            _render_table_image(pdf, pd.DataFrame(), x, y, w, h, title=title)
            return
        work = df[dimension_cols + metric_cols].copy()
        aggregation = str(options.get('aggregation') or 'sum').lower()
        if aggregation not in {'sum', 'mean', 'median', 'min', 'max', 'count'}:
            raise VisualBindingError(f'Unsupported aggregation: {aggregation}')
        if aggregation != 'count':
            for column in metric_cols:
                work[column] = pd.to_numeric(work[column], errors='coerce')
        work = work.dropna(subset=metric_cols, how='all')
        groups = work.groupby(dimension_cols, dropna=False)[metric_cols]
        grouped = (groups.sum(min_count=1) if aggregation == 'sum' else groups.agg(aggregation)).reset_index()
        first_metric = _library_resolve_column(grouped, options.get('sort_by')) or metric_cols[0]
        if options.get('sort', 'desc') != 'none':
            grouped = grouped.sort_values(first_metric, ascending=options.get('sort') == 'asc' if 'sort' in options else not bool(options.get('sort_desc', True)))
        limit = int(options.get('top_n') or options.get('limit') or 8)
        selected = grouped.head(limit)
        if options.get('show_totals'):
            if aggregation not in {'sum', 'count'}:
                raise VisualBindingError('Totals require an additive aggregation (sum or count)')
            total = {column: '' for column in dimension_cols}
            total[dimension_cols[0]] = 'Total'
            total.update({column: grouped[column].sum(min_count=1) for column in metric_cols})
            selected = pd.concat([selected, pd.DataFrame([total])], ignore_index=True)
        _render_table_image(pdf, _library_display_dataframe(selected, options), x, y, w, h, title=title, options=options)

    def render_detail_table(pdf, dataframes, x, y, w, h, *, columns=None, source=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        selected = _library_resolve_columns(df, columns or [])
        if not selected:
            selected = list(df.columns)[:6]
        sort_col = _library_resolve_column(df, options.get('sort_by')) if options.get('sort_by') else selected[0]
        if options.get('sort_by') and not sort_col:
            raise VisualBindingError('Detail table sort column was not found')
        if sort_col and options.get('sort', 'desc') != 'none':
            df = df.sort_values(sort_col, ascending=options.get('sort') == 'asc' if 'sort' in options else not bool(options.get('sort_desc', True)))
        limit = int(options.get('top_n') or options.get('limit') or 10)
        _render_table_image(pdf, _library_display_dataframe(df[selected].head(limit), options), x, y, w, h, title=title, options=options)

    def render_visual_component(pdf, dataframes, artifact, x, y, w, h):
        artifact = artifact or {}
        with _component_visual_context(artifact):
            return _render_bound_visual_component(pdf, dataframes, artifact, x, y, w, h)

    def _render_bound_visual_component(pdf, dataframes, artifact, x, y, w, h):
        preset_id = str(artifact.get('component_preset_id') or '')
        if preset_id not in PRESET_ROLES:
            raise VisualBindingError('Unknown visual preset')
        try:
            raw_options = {**PRESET_OPTIONS[preset_id], **(artifact.get('visual_options') or {})}
            options = VisualOptions.model_validate(raw_options).model_dump(exclude_none=True)
            default_sort = ('desc' if options.get('sort_desc', True) else 'asc') if preset_id.startswith(('bar_', 'table_')) else 'none'
            binding = DataBinding.model_validate({'sort': default_sort, **(artifact.get('data_binding') or {})}).model_dump()
        except ValueError as exc:
            raise VisualBindingError('Visual configuration failed contract validation') from exc
        if not binding.get('source') and len(dataframes) > 1:
            raise VisualBindingError('Visuals with multiple datasets require an explicit source')
        df = _library_first_dataframe(dataframes, binding.get('source'))
        for role in PRESET_ROLES[preset_id]:
            value = binding['roles'].get(role['name'])
            if not value:
                if role.get('required'):
                    raise VisualBindingError(f"Missing visual role: {role['name']}")
                continue
            values = value if isinstance(value, list) else [value]
            if isinstance(value, list) and not role.get('multiple'):
                raise VisualBindingError(f"Role requires a single column: {role['name']}")
            for column in values:
                parts = re.split(r'(?<!:):(?!:)', column, maxsplit=1)
                if len(parts) > 1 and parts[0] != binding.get('source'):
                    raise VisualBindingError('Visual roles cannot mix data sources without preprocessing')
                if _library_resolve_column(df, column) is None:
                    raise VisualBindingError(f'Visual column not found: {column}')
        _COMPONENT_OUTCOME['render_mode'] = 'library'
        options['filters'] = binding.get('filters') or options.get('filters') or []
        if binding.get('aggregation') and 'aggregation' not in options:
            options['aggregation'] = binding.get('aggregation')
        if binding.get('limit') and 'limit' not in options:
            options['limit'] = binding.get('limit')
            options['top_n'] = min(options.get('top_n') or binding['limit'], binding['limit'])
            if preset_id in {'bar_ranked', 'bar_grouped'}:
                options['aggregate_others'] = False
        options['sort'] = binding['sort']
        source = binding.get('source')
        roles = binding.get('roles') or {}
        title = options.get('title') or artifact.get('indicator') or artifact.get('id')

        if preset_id == 'bar_grouped':
            return render_bar_chart(pdf, dataframes, x, y, w, h, category=roles.get('category'), value=roles.get('value'), series=roles.get('series'), source=source, title=title, options=options)
        if preset_id == 'line_timeseries':
            return render_line_chart(pdf, dataframes, x, y, w, h, date=roles.get('date'), value=roles.get('value'), series=roles.get('series'), source=source, title=title, options=options)
        if preset_id == 'scatter_relationship':
            return render_scatter_plot(pdf, dataframes, x, y, w, h, x_value=roles.get('x'), y_value=roles.get('y'), color=roles.get('color'), source=source, title=title, options=options)
        if preset_id == 'radial_donut':
            return render_donut_chart(pdf, dataframes, x, y, w, h, category=roles.get('category'), value=roles.get('value'), source=source, title=title, options=options)
        if preset_id == 'radial_gauge':
            return render_gauge_chart(pdf, dataframes, x, y, w, h, value=roles.get('value'), target=roles.get('target'), source=source, title=title, options=options)
        if preset_id == 'table_summary':
            return render_summary_table(pdf, dataframes, x, y, w, h, dimensions=_library_role_list(binding, 'dimensions'), metrics=_library_role_list(binding, 'metrics'), source=source, title=title, options=options)
        if preset_id == 'table_detail':
            return render_detail_table(pdf, dataframes, x, y, w, h, columns=_library_role_list(binding, 'columns'), source=source, title=title, options=options)
        return render_bar_chart(pdf, dataframes, x, y, w, h, category=roles.get('category'), value=roles.get('value'), source=source, title=title, options=options)
    """).strip()
