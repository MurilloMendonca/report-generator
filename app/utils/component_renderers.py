import json
import re
from textwrap import dedent


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
    - Advanced base helpers via `render_runtime`: `_library_dataframe`, `_library_resolve_column`, `_library_resolve_columns`, `_library_aggregate`, `_library_limit_top`, `_library_empty_chart`.

    Primary signatures:
    {chr(10).join(f"- {signature}" for signature in signatures)}

    Preset guidance:
    - {preset_hint}

    Customization rule:
    - Keep generated code small. Do not paste runtime helper bodies into the component function.
    - If the standard helper is close, prepare/transform `options`, `artifact`, or a small dataframe and call the helper.
    - For translated or friendly labels, prefer `artifact["visual_options"]["labels"]`; do not rename dataframe columns just to change axis labels, legends, or table headers.
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
        try:
            return render_visual_component(pdf, dataframes, artifact, x, y, w, h)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Library renderer failed for {suffix}; falling back to generated code')
            generated = globals().get('render_{suffix}_generated')
            if generated is None:
                raise
            try:
                return generated(pdf, dataframes, x, y, w, h, df=df)
            except TypeError:
                return generated(pdf, dataframes, x, y, w, h)
    """).strip()


def sandbox_runtime_source() -> str:
    return dedent(r"""
    import os
    import re
    import tempfile
    import textwrap

    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns


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
            axis.grid(color=theme.get('grid_color', '#e5e7eb'), alpha=0.7)
            for spine in axis.spines.values():
                spine.set_color(theme.get('grid_color', '#e5e7eb'))
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
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            fig.savefig(tmp.name, dpi=300)
            plt.close(fig)
            pdf.image(tmp.name, x=x, y=y, w=w, h=h)
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
        return '\n'.join(lines[:2])


    def _set_axis_chart_title(ax, title, w=None):
        wrapped = _wrapped_chart_title(title, w)
        if not wrapped:
            return 0
        line_count = wrapped.count('\n') + 1
        ax.set_title(
            wrapped,
            loc='center',
            pad=CHART_TITLE_PAD,
            fontsize=CHART_TITLE_FONTSIZE,
            fontweight=CHART_TITLE_FONTWEIGHT,
            color=CHART_THEME.get('text_color', '#111827'),
        )
        return line_count


    def _chart_top_margin(title_lines):
        return 0.88 if title_lines <= 1 else 0.80


    def _table_bbox_for_title(title_lines):
        if not title_lines:
            return [0.0, 0.06, 1.0, 0.88]
        return [0.0, 0.04, 1.0, 0.82 if title_lines <= 1 else 0.78]


    def _render_table_image(pdf, dataframe, x, y, w, h, title=None, theme=None):
        theme = theme or CHART_THEME
        table_theme = theme.get('table', {})
        df = dataframe.copy() if dataframe is not None else pd.DataFrame()
        max_rows = 5 if h < 60 else 8 if h < 95 else 12
        max_cols = 4 if w < 100 else 6 if w < 150 else 8
        df = df.iloc[:max_rows, :max_cols]
        df = df.fillna('')
        for column in df.columns:
            df[column] = df[column].map(
                lambda value: _format_compact_number(value)
                if isinstance(value, (int, float, np.integer, np.floating))
                else str(value)[:42]
            )
        fig, ax = plt.subplots(figsize=(w / 25.4, h / 25.4))
        ax.axis('off')
        title_lines = _set_axis_chart_title(ax, title, w)
        if df.empty:
            ax.text(0.5, 0.5, 'Dados indisponiveis', ha='center', va='center', fontsize=CHART_LABEL_FONTSIZE)
            _save_chart_to_pdf(fig, pdf, x, y, w, h)
            return
        table_bbox = _table_bbox_for_title(title_lines)
        table = ax.table(
            cellText=df.values,
            colLabels=[str(c)[:28] for c in df.columns],
            cellLoc='center',
            loc='upper center',
            bbox=table_bbox,
        )
        table.auto_set_font_size(False)
        font_size = max(4, CHART_TABLE_FONTSIZE - (1 if h < 65 else 0) - (1 if len(df.columns) > 5 else 0))
        table.set_fontsize(font_size)
        for (row, col), cell in table.get_celld().items():
            cell.set_edgecolor(table_theme.get('border', '#d1d5db'))
            cell.set_linewidth(0.4)
            if row == 0:
                cell.set_facecolor(table_theme.get('header_bg', '#111827'))
                cell.get_text().set_color(table_theme.get('header_text', '#ffffff'))
                cell.get_text().set_fontweight('bold')
            else:
                cell.set_facecolor(table_theme.get('alt_row_bg', '#f9fafb') if row % 2 == 0 else table_theme.get('row_bg', '#ffffff'))
                cell.get_text().set_color(theme.get('text_color', '#111827'))
        fig.subplots_adjust(left=0.02, right=0.98, top=_chart_top_margin(title_lines) if title_lines else 0.98, bottom=0.02)
        _save_chart_to_pdf(fig, pdf, x, y, w, h)


    def _install_standard_chart_style():
        global _STANDARD_CHART_STYLE_INSTALLED
        if _STANDARD_CHART_STYLE_INSTALLED:
            return
        from matplotlib.axes import Axes
        original_set_title = Axes.set_title
        original_set_xlabel = Axes.set_xlabel
        original_set_ylabel = Axes.set_ylabel
        original_tick_params = Axes.tick_params
        original_legend = Axes.legend
        original_plt_legend = plt.legend

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
            kwargs['labelsize'] = CHART_TICK_FONTSIZE
            return original_tick_params(self, axis=axis, **kwargs)

        def legend(self, *args, **kwargs):
            kwargs['fontsize'] = CHART_LEGEND_FONTSIZE
            return original_legend(self, *args, **kwargs)

        def plt_legend(*args, **kwargs):
            kwargs['fontsize'] = CHART_LEGEND_FONTSIZE
            return original_plt_legend(*args, **kwargs)

        Axes.set_title = set_title
        Axes.set_xlabel = set_xlabel
        Axes.set_ylabel = set_ylabel
        Axes.tick_params = tick_params
        Axes.legend = legend
        plt.legend = plt_legend
        _STANDARD_CHART_STYLE_INSTALLED = True
    """).strip() + "\n\n" + library_renderer_source() + "\n"


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
            text = text.split(':', 1)[1]
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
                continue
            column = _library_resolve_column(result, item.get('column'))
            if not column:
                continue
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
                    result = result[result[column].astype(str).str.contains(str(value), case=False, na=False)]
            except Exception:
                continue
        return result

    def _library_dataframe(dataframes, binding):
        binding = binding or {}
        df = _library_first_dataframe(dataframes, binding.get('source'))
        return _library_apply_filters(df, binding.get('filters'))

    def _library_empty_chart(pdf, x, y, w, h, title=None, message='Dados indisponiveis'):
        fig, ax = plt.subplots(figsize=(max(w, 20) / 25.4, max(h, 20) / 25.4))
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
            aggregation = 'sum'
        if aggregation != 'count':
            work[value_col] = pd.to_numeric(work[value_col], errors='coerce')
        work = work.dropna(subset=[value_col])
        if work.empty:
            return pd.DataFrame(columns=category_cols + [value_col])
        grouped = work.groupby(category_cols, dropna=False)[value_col].agg(aggregation).reset_index()
        return grouped

    def _library_limit_top(df, value_col, top_n=None, aggregate_others=False, category_col=None):
        if df.empty or value_col not in df.columns:
            return df
        result = df.sort_values(value_col, ascending=False)
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
        fig, ax = plt.subplots(figsize=(max(w, 20) / 25.4, max(h, 20) / 25.4))

        if series_col:
            grouped = _library_aggregate(df, [category_col, series_col], value_col, aggregation)
            if grouped.empty:
                _library_empty_chart(pdf, x, y, w, h, title)
                return
            totals = grouped.groupby(category_col)[value_col].sum().sort_values(ascending=False)
            categories = list(totals.index[: int(top_n or 8)])
            grouped = grouped[grouped[category_col].isin(categories)]
            pivot = grouped.pivot_table(index=category_col, columns=series_col, values=value_col, aggfunc='sum', fill_value=0)
            pivot = pivot.reindex(categories)
            pivot.plot(kind='bar', ax=ax, color=_chart_colors(len(pivot.columns)))
            ax.set_xlabel('')
            ax.set_ylabel(measure_label)
            if len(str(category_col)) < 18:
                ax.set_xlabel(_library_display_label(category_col, options))
            ax.legend(title=_library_display_label(series_col, options), loc='upper left', bbox_to_anchor=(1.01, 1.0), frameon=False)
            ax.tick_params(axis='x', rotation=30)
        else:
            grouped = _library_aggregate(df, [category_col], value_col, aggregation)
            grouped = _library_limit_top(grouped, value_col, top_n, bool(options.get('aggregate_others')), category_col)
            if grouped.empty:
                _library_empty_chart(pdf, x, y, w, h, title)
                return
            labels = [str(item)[:32] for item in grouped[category_col].fillna('')]
            values = grouped[value_col].fillna(0).tolist()
            if orientation == 'vertical':
                bars = ax.bar(labels, values, color=colors[: len(values)])
                ax.tick_params(axis='x', rotation=30)
                ax.set_ylabel(measure_label)
                if show_values:
                    ax.bar_label(bars, labels=[_format_compact_number(v) for v in values], padding=2, fontsize=CHART_LABEL_FONTSIZE)
            else:
                bars = ax.barh(labels, values, color=colors[: len(values)])
                ax.invert_yaxis()
                ax.set_xlabel(measure_label)
                if show_values:
                    ax.bar_label(bars, labels=[_format_compact_number(v) for v in values], padding=2, fontsize=CHART_LABEL_FONTSIZE)

        title_lines = _set_axis_chart_title(ax, title, w)
        fig.tight_layout(pad=0.8, rect=(0, 0, 1, _chart_top_margin(title_lines) if title_lines else 1))
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
        work = df[[date_col, value_col] + ([series_col] if series_col else [])].copy()
        work[date_col] = pd.to_datetime(work[date_col], errors='coerce')
        work[value_col] = pd.to_numeric(work[value_col], errors='coerce')
        work = work.dropna(subset=[date_col, value_col])
        if work.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        aggregation = str(options.get('aggregation') or 'sum').lower()
        if aggregation not in {'sum', 'mean', 'median', 'min', 'max', 'count'}:
            aggregation = 'sum'
        fig, ax = plt.subplots(figsize=(max(w, 20) / 25.4, max(h, 20) / 25.4))
        marker = 'o' if options.get('show_markers', True) else None
        if series_col:
            grouped = work.groupby([date_col, series_col], dropna=False)[value_col].agg(aggregation).reset_index()
            pivot = grouped.pivot_table(index=date_col, columns=series_col, values=value_col, aggfunc='sum').sort_index()
            pivot.plot(ax=ax, marker=marker, linewidth=1.8, color=_chart_colors(len(pivot.columns)))
            ax.legend(title=_library_display_label(series_col, options), loc='upper left', bbox_to_anchor=(1.01, 1.0), frameon=False)
        else:
            grouped = work.groupby(date_col, dropna=False)[value_col].agg(aggregation).reset_index().sort_values(date_col)
            ax.plot(grouped[date_col], grouped[value_col], marker=marker, linewidth=1.9, color=_chart_colors(1)[0])
        title_lines = _set_axis_chart_title(ax, title, w)
        ax.set_xlabel('')
        ax.set_ylabel(_library_display_label(value_col, options))
        ax.tick_params(axis='x', rotation=30)
        fig.tight_layout(pad=0.8, rect=(0, 0, 1, _chart_top_margin(title_lines) if title_lines else 1))
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
        if len(work) > max_points:
            work = work.sample(max_points, random_state=7)
        if work.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        fig, ax = plt.subplots(figsize=(max(w, 20) / 25.4, max(h, 20) / 25.4))
        if color_col:
            categories = list(work[color_col].dropna().astype(str).unique())[:8]
            colors = _chart_colors(len(categories))
            for index, category in enumerate(categories):
                subset = work[work[color_col].astype(str) == category]
                ax.scatter(subset[x_col], subset[y_col], s=24, alpha=0.72, color=colors[index], label=str(category)[:24])
            ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1.0), frameon=False)
        else:
            ax.scatter(work[x_col], work[y_col], s=24, alpha=0.72, color=_chart_colors(1)[0])
        if options.get('show_trendline', True) and len(work) >= 3:
            try:
                coeffs = np.polyfit(work[x_col], work[y_col], 1)
                xs = np.array([work[x_col].min(), work[x_col].max()])
                ax.plot(xs, coeffs[0] * xs + coeffs[1], color=_chart_colors(2)[1], linewidth=1.3)
            except Exception:
                pass
        title_lines = _set_axis_chart_title(ax, title, w)
        ax.set_xlabel(_library_display_label(x_col, options))
        ax.set_ylabel(_library_display_label(y_col, options))
        fig.tight_layout(pad=0.8, rect=(0, 0, 1, _chart_top_margin(title_lines) if title_lines else 1))
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
        grouped = _library_aggregate(df, [category_col], value_col, options.get('aggregation') or 'sum')
        grouped = _library_limit_top(grouped, value_col, options.get('top_n') or 5, bool(options.get('aggregate_others')), category_col)
        grouped = grouped[grouped[value_col] > 0]
        if grouped.empty:
            _library_empty_chart(pdf, x, y, w, h, title)
            return
        labels = [str(item)[:24] for item in grouped[category_col].fillna('')]
        values = grouped[value_col].tolist()
        fig, ax = plt.subplots(figsize=(max(w, 20) / 25.4, max(h, 20) / 25.4))
        autopct = '%1.0f%%' if options.get('show_percent', True) else None
        ax.pie(values, labels=labels, colors=_chart_colors(len(values)), startangle=90, autopct=autopct, wedgeprops={'width': 0.42, 'edgecolor': 'white'})
        ax.text(0, 0, _format_compact_number(sum(values)), ha='center', va='center', fontsize=CHART_TITLE_FONTSIZE, fontweight=CHART_TITLE_FONTWEIGHT)
        title_lines = _set_axis_chart_title(ax, title, w)
        ax.axis('equal')
        fig.tight_layout(pad=0.8, rect=(0, 0, 1, _chart_top_margin(title_lines) if title_lines else 1))
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
        actual = pd.to_numeric(df[value_col], errors='coerce').dropna().sum()
        if target_col:
            maximum = pd.to_numeric(df[target_col], errors='coerce').dropna().sum()
        else:
            maximum = options.get('target') or options.get('max_value') or max(actual, 1)
        try:
            maximum = float(maximum)
        except Exception:
            maximum = max(actual, 1)
        ratio = 0 if maximum <= 0 else max(0, min(1, float(actual) / maximum))
        from matplotlib.patches import Wedge
        fig, ax = plt.subplots(figsize=(max(w, 20) / 25.4, max(h, 20) / 25.4))
        ax.set_xlim(-1.2, 1.2)
        ax.set_ylim(-0.2, 1.2)
        ax.axis('off')
        ax.add_patch(Wedge((0, 0), 1, 0, 180, width=0.28, facecolor=CHART_THEME.get('grid_color', '#e5e7eb'), edgecolor='white'))
        ax.add_patch(Wedge((0, 0), 1, 0, 180 * ratio, width=0.28, facecolor=_chart_colors(1)[0], edgecolor='white'))
        label = f"{ratio * 100:.0f}%" if options.get('show_center_value', True) else _format_compact_number(actual)
        ax.text(0, 0.2, label, ha='center', va='center', fontsize=max(11, CHART_TITLE_FONTSIZE + 4), fontweight='bold')
        ax.text(0, -0.04, _format_compact_number(actual), ha='center', va='center', fontsize=CHART_LABEL_FONTSIZE, color=CHART_THEME.get('muted_text_color', '#6b7280'))
        title_lines = _set_axis_chart_title(ax, title, w)
        fig.tight_layout(pad=0.8, rect=(0, 0, 1, _chart_top_margin(title_lines) if title_lines else 1))
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
            aggregation = 'sum'
        for column in metric_cols:
            work[column] = pd.to_numeric(work[column], errors='coerce')
        grouped = work.groupby(dimension_cols, dropna=False)[metric_cols].agg(aggregation).reset_index()
        first_metric = metric_cols[0]
        if first_metric in grouped.columns:
            grouped = grouped.sort_values(first_metric, ascending=not bool(options.get('sort_desc', True)))
        limit = int(options.get('top_n') or options.get('limit') or 8)
        _render_table_image(pdf, _library_display_dataframe(grouped.head(limit), options), x, y, w, h, title=title)

    def render_detail_table(pdf, dataframes, x, y, w, h, *, columns=None, source=None, title=None, options=None):
        options = options or {}
        binding = {'source': source, 'filters': options.get('filters') or []}
        df = _library_dataframe(dataframes, binding)
        selected = _library_resolve_columns(df, columns or [])
        if not selected:
            selected = list(df.columns)[:6]
        limit = int(options.get('top_n') or options.get('limit') or 10)
        _render_table_image(pdf, _library_display_dataframe(df[selected].head(limit), options), x, y, w, h, title=title)

    def render_visual_component(pdf, dataframes, artifact, x, y, w, h):
        artifact = artifact or {}
        binding = artifact.get('data_binding') or {}
        options = dict(artifact.get('visual_options') or {})
        options['filters'] = binding.get('filters') or options.get('filters') or []
        if binding.get('aggregation') and 'aggregation' not in options:
            options['aggregation'] = binding.get('aggregation')
        if binding.get('limit') and 'limit' not in options:
            options['limit'] = binding.get('limit')
        source = binding.get('source')
        roles = binding.get('roles') or {}
        preset_id = str(artifact.get('component_preset_id') or '')
        title = artifact.get('indicator') or artifact.get('id')

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
