"""Checks bundled into the sandbox runtime, operating on the actual chart pixels."""
from functools import lru_cache

from matplotlib import font_manager, ft2font
from matplotlib.backends.backend_agg import FigureCanvasAgg


class InvisibleTextError(ValueError):
    render_status = 'failed'
    render_code = 'invisible_text'


@lru_cache(maxsize=64)
def _font_characters(filename):
    return frozenset(ft2font.FT2Font(filename).get_charmap())


def check_chart_output(fig):
    """Compare the final bitmap with text removed, including every table cell.

    Geometry alone cannot detect a stale clip path or white text on white.
    Two draws, independent of the number of cells, keep this check inexpensive.
    """
    FigureCanvasAgg(fig)
    fig.set_dpi(300)
    fig.canvas.draw()
    texts = list(fig.texts)
    for legend in fig.legends:
        texts.extend(legend.get_texts())
        texts.append(legend.get_title())
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        texts.extend(ax.texts)
        texts.extend([ax.title, ax._left_title, ax._right_title])
        if ax.axison:
            texts.extend([ax.xaxis.label, ax.yaxis.label, ax.xaxis.get_offset_text(), ax.yaxis.get_offset_text()])
            # Locator APIs also return labels beyond the axis limits. Only
            # ticks selected for drawing belong to the exported figure.
            for axis in (ax.xaxis, ax.yaxis):
                for tick in axis._update_ticks():
                    texts.extend([tick.label1, tick.label2])
        if ax.get_legend():
            texts.extend(ax.get_legend().get_texts())
            texts.append(ax.get_legend().get_title())
        for table in ax.tables:
            texts.extend(cell.get_text() for cell in table.get_celld().values())
    texts = list({id(text): text for text in texts if text.get_text().strip() and text.get_visible()}.values())
    renderer = fig.canvas.get_renderer()
    bounds = [text.get_window_extent(renderer) for text in texts]
    for text in texts:
        # MathText uses several fonts; its parser owns glyph selection.
        if text.get_usetex() or (text.get_parse_math() and text.get_text().count('$') >= 2):
            continue
        characters = _font_characters(font_manager.findfont(text.get_fontproperties()))
        missing = {char for char in text.get_text() if not char.isspace() and ord(char) not in characters}
        if missing:
            raise MissingGlyphError('Chart font cannot render: ' + ', '.join(f'U+{ord(char):04X}' for char in sorted(missing)))
    visible = np.asarray(fig.canvas.buffer_rgba()).copy()
    try:
        for text in texts:
            text.set_visible(False)
        fig.canvas.draw()
        without_text = np.asarray(fig.canvas.buffer_rgba()).copy()
    finally:
        for text in texts:
            text.set_visible(True)
    difference = np.max(np.abs(visible.astype(np.int16) - without_text.astype(np.int16)), axis=2) > 8
    height, width = difference.shape
    for text, bbox in zip(texts, bounds):
        left, right = max(0, int(np.floor(bbox.x0))), min(width, int(np.ceil(bbox.x1)))
        top, bottom = max(0, int(np.floor(height - bbox.y1))), min(height, int(np.ceil(height - bbox.y0)))
        if right <= left or bottom <= top or np.count_nonzero(difference[top:bottom, left:right]) < 2:
            raise InvisibleTextError('Chart/table text has no visible pixels: ' + text.get_text()[:100])
    _COMPONENT_OUTCOME['pixel_text_count'] = _COMPONENT_OUTCOME.get('pixel_text_count', 0) + len(texts)
    _COMPONENT_OUTCOME['glyph_check'] = True
    return visible
