"""Measured chart layout, bundled into the isolated renderer runtime.

The runtime supplies typography, text measurement and render-outcome helpers.
This module contains no report data or application dependencies.
"""


def record_render_warning(message):
    warnings = _COMPONENT_OUTCOME.setdefault('warnings', [])
    if message not in warnings:
        warnings.append(message)


def _chart_label(text, width, measure, lines=2, warn=True):
    text = ' '.join(str(text).split())
    wrapped, shortened = _wrap_table_text(text, width, measure, lines)
    if shortened:
        # Retain identifying suffixes when categories share a long prefix.
        low, high = 0, len(text)
        while low < high:
            count = (low + high + 1) // 2
            start = (count + 1) // 2
            candidate = text[:start] + '…' + (text[-(count - start):] if count > start else '')
            _, omitted = _wrap_table_text(candidate, width, measure, lines)
            if omitted:
                high = count - 1
            else:
                low = count
        start = (low + 1) // 2
        candidate = text[:start] + '…' + (text[-(low - start):] if low > start else '')
        wrapped, _ = _wrap_table_text(candidate, width, measure, lines)
        if warn:
            record_render_warning('Long chart labels were shortened with ellipses to fit the layout slot')
    return wrapped


def _chart_legend(fig, ax, renderer, top, bottom, min_plot_height):
    legend = ax.get_legend()
    if legend is None:
        return top, bottom, 0.975
    handles = legend.legend_handles
    labels = [text.get_text() for text in legend.get_texts()]
    title = legend.get_title().get_text()
    legend.remove()
    count = len(labels)
    if not count:
        return top, bottom, 0.975
    width, height = fig.bbox.width, fig.bbox.height
    base = 5 if CHART_STYLE.get('id') == 'dense_dashboard' else CHART_LEGEND_FONTSIZE
    for line_limit in (3, 2, 1):
        for size in sorted({float(base), 5.0, 4.5}, reverse=True):
            measure = _table_text_measurer(fig, renderer, size)
            choices = [('right', 1)] if fig.get_figwidth() * 25.4 >= 110 else []
            choices += [('bottom', cols) for cols in range(min(3 if width > 600 else 2, count), 0, -1)]
            for position, columns in choices:
                cell_width = width * (0.30 if position == 'right' else 0.92 / columns) - renderer.points_to_pixels(15)
                display = [_chart_label(label, max(15, cell_width), measure, line_limit, warn=False) for label in labels]
                if len(set(display)) < len(set(labels)):
                    display = [f'{index + 1}. {label}' for index, label in enumerate(display)]
                legend = ax.legend(handles, display, title=_chart_label(title, width * 0.85, measure, 1, warn=False) if title else None,
                                   loc='upper right' if position == 'right' else 'lower center',
                                   bbox_to_anchor=(0.975, top) if position == 'right' else (0.5, bottom),
                                   bbox_transform=fig.transFigure, ncol=columns, frameon=False,
                                   prop={'size': size}, title_fontsize=size,
                                   handlelength=1.2, handletextpad=0.5, columnspacing=0.8,
                                   labelspacing=0.4, borderpad=0.1, borderaxespad=0, markerscale=0.7)
                legend.set_in_layout(False)
                for text in [*legend.get_texts(), legend.get_title()]:
                    text.set_parse_math(False)
                    text.set_usetex(False)
                bounds = legend.get_window_extent(renderer)
                if position == 'right':
                    right = bounds.x0 / width - renderer.points_to_pixels(4) / width
                    if bounds.height <= (top - bottom) * height and right >= 0.58:
                        if any('…' in label for label in display):
                            record_render_warning('Long legend labels were shortened with ellipses to fit the layout slot')
                        return top, bottom, right
                else:
                    new_bottom = bounds.y1 / height + renderer.points_to_pixels(4) / height
                    reserve = max(min_plot_height + renderer.points_to_pixels(32 if ax.axison else 6), (top - bottom) * height * 0.55)
                    if bounds.width <= width * 0.95 and (top - new_bottom) * height >= reserve:
                        if any('…' in label for label in display):
                            record_render_warning('Long legend labels were shortened with ellipses to fit the layout slot')
                        return top, new_bottom, 0.975
                legend.remove()
    return None


def _chart_tick_labels(fig, ax, renderer, category_axis, raw_labels):
    width, height = ax.get_window_extent(renderer).size
    if category_axis and raw_labels:
        axis = ax.yaxis if category_axis == 'y' else ax.xaxis
        ticks = axis.get_ticklabels()
        size = min((tick.get_fontsize() for tick in ticks), default=CHART_TICK_FONTSIZE)
        measure = _table_text_measurer(fig, renderer, size)
        if category_axis == 'y':
            line_height = max(1, measure('Ag')[1] * 1.2)
            lines = max(1, min(3, int(height / max(1, len(raw_labels)) / line_height)))
            label_width = fig.bbox.width * 0.33
        else:
            lines = 2
            label_width = max(renderer.points_to_pixels(12), width / max(1, len(raw_labels)) * 0.9)
        display = [_chart_label(label, label_width, measure, lines) for label in raw_labels]
        if len(set(display)) < len(set(raw_labels)):
            display = [_chart_label(f'{index + 1}. {label}', label_width, measure, lines) for index, label in enumerate(raw_labels)]
        axis.set_ticklabels(display)
        for tick in axis.get_ticklabels():
            tick.set_rotation(0)
            tick.set_parse_math(False)
    for axis, available in ((ax.xaxis, width), (ax.yaxis, height)):
        measure = _table_text_measurer(fig, renderer, axis.label.get_fontsize())
        original = getattr(axis, '_report_label', axis.label.get_text())
        axis._report_label = original
        axis.label.set_text(_chart_label(original, available, measure, 2))
        axis.label.set_parse_math(False)


def _fit_chart_annotations(fig, ax):
    from matplotlib.text import Annotation
    renderer = fig.canvas.get_renderer()
    area = ax.get_window_extent(renderer).padded(-renderer.points_to_pixels(1))
    occupied = []
    omitted = 0
    for text in ax.texts:
        if not isinstance(text, Annotation) or not text.get_visible():
            continue
        try:
            finite = np.isfinite([ax.convert_xunits(text.xy[0]), ax.convert_yunits(text.xy[1])]).all()
        except (TypeError, ValueError):
            finite = False
        if not finite:
            text.set_visible(False)
            omitted += 1
            continue
        text.set_parse_math(False)
        text.set_in_layout(False)
        original = text.get_position()
        placed = False
        for size in sorted({text.get_fontsize(), 5.0}, reverse=True):
            text.set_fontsize(size)
            for dy in (0, 5, -7, 10):
                text.set_position(original)
                bounds = text.get_window_extent(renderer)
                if not np.isfinite(bounds.extents).all():
                    continue
                dx = max(0, area.x0 - bounds.x0) - max(0, bounds.x1 - area.x1)
                shift_y = max(0, area.y0 - bounds.y0) - max(0, bounds.y1 - area.y1) + renderer.points_to_pixels(dy)
                position = text.get_transform().transform(original) + np.array([dx, shift_y])
                text.set_position(text.get_transform().inverted().transform(position))
                bounds = text.get_window_extent(renderer)
                if area.contains(bounds.x0, bounds.y0) and area.contains(bounds.x1, bounds.y1) and not any(bounds.padded(1).overlaps(other) for other in occupied):
                    occupied.append(bounds)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            text.set_visible(False)
            omitted += 1
    if omitted:
        record_render_warning('Some point/value labels were omitted to avoid overlap; plotted data is unchanged')


def _fit_chart_tick_density(fig, ax, category_axis):
    renderer = fig.canvas.get_renderer()
    omitted = False
    for direction, axis in (('x', ax.xaxis), ('y', ax.yaxis)):
        if direction == category_axis or not ax.axison:
            continue
        lower, upper = sorted(axis.get_view_interval())
        occupied = []
        for tick in [*axis.get_major_ticks(), *axis.get_minor_ticks()]:
            text = tick.label1
            if not text.get_visible() or not text.get_text() or not lower <= tick.get_loc() <= upper:
                continue
            bounds = text.get_window_extent(renderer).padded(renderer.points_to_pixels(1))
            if any(bounds.overlaps(other) for other in occupied):
                text.set_visible(False)
                omitted = True
            else:
                occupied.append(bounds)
    if omitted:
        record_render_warning('Some axis tick labels were omitted to avoid overlap; plotted data is unchanged')


def _chart_too_small(fig, ax):
    ax.clear()
    ax.axis('off')
    for text in fig.texts:
        text.set_visible(False)
    ax.set_position([0.05, 0.05, 0.9, 0.9])
    ax.text(0.5, 0.5, 'Amplie o gráfico', transform=ax.transAxes, ha='center', va='center', fontsize=5, clip_on=True)
    _COMPONENT_OUTCOME.update(status='empty', code='layout_too_small', message='Chart slot is too small; enlarge it or select fewer categories/series')
    return False


def _fit_chart_layout(fig, ax, w=None, h=None, category_axis=None, category_labels=None):
    _apply_chart_theme(fig)
    fig.set_dpi(300)
    renderer = fig.canvas.get_renderer()
    width, height = fig.bbox.size
    gap = renderer.points_to_pixels(3)
    title = ax.get_title()
    top, bottom = 0.975, 0.025
    if title:
        size = ax.title.get_fontsize()
        measure = _table_text_measurer(fig, renderer, size, 'bold')
        wrapped, shortened = _wrap_table_text(title, width * 0.94, measure, 2)
        if shortened:
            record_render_warning('Chart title was shortened with an ellipsis to fit the layout slot')
        ax.title.set_visible(False)
        heading = fig.text(0.5, top, wrapped, ha='center', va='top', fontsize=size, fontweight='bold',
                           color=CHART_THEME.get('text_color', '#111827'), usetex=False, parse_math=False)
        top = (heading.get_window_extent(renderer).y0 - gap) / height
    min_height = renderer.points_to_pixels(26 if ax.axison else 18)
    if category_axis == 'y' and category_labels:
        tick_size = 5 if CHART_STYLE.get('id') == 'dense_dashboard' else CHART_TICK_FONTSIZE
        min_height = max(min_height, renderer.points_to_pixels(len(category_labels) * tick_size * 1.2))
    bounds = _chart_legend(fig, ax, renderer, top, bottom, min_height)
    if bounds is None:
        return _chart_too_small(fig, ax)
    top, bottom, right = bounds
    outer = np.array([0.025 * width, bottom * height, right * width, top * height])
    margins = np.array([renderer.points_to_pixels(18), renderer.points_to_pixels(14), 0.0, 0.0]) if ax.axison else np.zeros(4)
    from matplotlib.text import Annotation
    for text in ax.texts:
        if isinstance(text, Annotation):
            text.set_in_layout(False)
    for axis, direction in ((ax.xaxis, 'x'), (ax.yaxis, 'y')):
        if direction != category_axis and isinstance(axis.get_major_locator(), (mpl.ticker.AutoLocator, mpl.ticker.MaxNLocator)):
            axis.set_major_locator(mpl.ticker.MaxNLocator(nbins=3 if width < 1100 else 5))
    for _ in range(4):
        left, low = outer[:2] + margins[:2]
        high_right, high_top = outer[2:] - margins[2:]
        if high_right - left < renderer.points_to_pixels(18) or high_top - low < min_height:
            return _chart_too_small(fig, ax)
        ax.set_position([left / width, low / height, (high_right - left) / width, (high_top - low) / height])
        if ax.axison:
            _chart_tick_labels(fig, ax, renderer, category_axis, category_labels)
        fig.canvas.draw()
        actual = ax.get_window_extent(renderer)
        tight = ax.get_tightbbox(renderer) if ax.axison else actual
        needed = np.array([actual.x0 - tight.x0, actual.y0 - tight.y0, tight.x1 - actual.x1, tight.y1 - actual.y1]) + (gap if ax.axison else 0)
        margins = np.maximum(margins, needed)
    _fit_chart_tick_density(fig, ax, category_axis)
    _fit_chart_annotations(fig, ax)
    fig._report_fitted = True
    return True


def _fit_chart_value(fig, ax, text, width_fraction=0.85, height_fraction=0.22):
    renderer = fig.canvas.get_renderer()
    bounds = ax.get_window_extent(renderer)
    size = text.get_fontsize()
    while size >= 4.5:
        text.set_fontsize(size)
        text.set_parse_math(False)
        text.set_usetex(False)
        measured = text.get_window_extent(renderer)
        if measured.width <= bounds.width * width_fraction and measured.height <= bounds.height * height_fraction:
            return True
        size -= 0.5
    return _chart_too_small(fig, ax)
