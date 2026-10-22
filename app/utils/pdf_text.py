"""PDF text fitting shared by generation guidance and the sandbox renderer."""
import re
from pathlib import Path

import matplotlib

from fpdf import FPDF, XPos, YPos
from fpdf.enums import MethodReturnValue


class TextBoxTooSmallError(ValueError):
    render_status = 'empty'
    render_code = 'layout_too_small'


def safe_pdf_text(value):
    # Preserve currencies and punctuation. Missing glyphs must be reported,
    # never silently replaced by question marks.
    return str(value if value is not None else '').replace('\u00a0', ' ')


class MissingGlyphError(ValueError):
    render_status = 'failed'
    render_code = 'missing_glyph'


def configure_pdf_fonts(pdf, families=('helvetica', 'times', 'courier')):
    """Embed the fonts already shipped with Matplotlib; no system-font dependency."""
    registered = getattr(pdf, '_report_unicode_fonts', set())
    directory = Path(matplotlib.get_data_path()) / 'fonts' / 'ttf'
    for family, filename in (('helvetica', 'DejaVuSans'), ('times', 'DejaVuSerif'), ('courier', 'DejaVuSansMono')):
        if family not in families or family in registered:
            continue
        for style, suffix in (('', ''), ('B', '-Bold'), ('I', '-Oblique' if family != 'times' else '-Italic'),
                              ('BI', '-BoldOblique' if family != 'times' else '-BoldItalic')):
            pdf.add_font('report_' + family, style, str(directory / (filename + suffix + '.ttf')))
        registered.add(family)
    pdf._report_unicode_fonts = registered


def pdf_font_family(family):
    family = str(family or '').lower()
    family = 'helvetica' if family == 'arial' else family
    return 'report_' + family if family in {'helvetica', 'times', 'courier'} else family


class ReportPDF(FPDF):
    """Preserve standard font names in existing generated component code."""
    def set_font(self, family=None, style='', size=0):
        mapped = pdf_font_family(family)
        if mapped.startswith('report_'):
            configure_pdf_fonts(self, (mapped.removeprefix('report_'),))
        return super().set_font(mapped, style, size)

    def normalize_text(self, text):
        if self.current_font and self.is_ttf_font:
            check_pdf_glyphs(self, text)
        return super().normalize_text(text)


def check_pdf_glyphs(pdf, text):
    missing = sorted({char for char in str(text) if not char.isspace() and ord(char) not in pdf.current_font.cmap})
    if missing:
        raise MissingGlyphError('Font cannot render: ' + ', '.join(f'U+{ord(char):04X}' for char in missing))


def pdf_text_lines(pdf, text, width, line_height):
    return pdf.multi_cell(width, line_height, safe_pdf_text(text), dry_run=True, output=MethodReturnValue.LINES)


def fit_pdf_text(pdf, text, width, line_height, max_lines, sentences=False):
    text = safe_pdf_text(text).strip()
    if not text:
        return '', False
    if max_lines < 1 or width <= 2 * pdf.c_margin:
        return '', True
    if len(pdf_text_lines(pdf, text, width, line_height)) <= max_lines:
        return text, False
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + '...'
        if len(pdf_text_lines(pdf, candidate, width, line_height)) <= max_lines:
            low = middle
        else:
            high = middle - 1
    prefix = text[:low].rstrip()
    if low < len(text) and not text[low:low + 1].isspace() and ' ' in prefix:
        prefix = prefix.rsplit(' ', 1)[0]
    if sentences:
        endings = list(re.finditer(r'[.!?](?:\s|$)', prefix))
        if endings and endings[-1].end() >= len(prefix) * 0.5:
            prefix = prefix[:endings[-1].end()].rstrip()
    return prefix.rstrip(' .') + '...', True


def _pdf_text_layout(pdf, text, title, width, height, font_family, font_size, line_height):
    configure_pdf_fonts(pdf, (font_family.removeprefix('report_'),))
    font_family = pdf_font_family(font_family)
    line_height = max(float(line_height), font_size * 25.4 / 72 * 1.1)
    title_line_height = max(line_height, font_size * 25.4 / 72 * 1.2)
    gap = 1 if title else 0
    pdf.set_font(font_family, 'B', font_size)
    check_pdf_glyphs(pdf, title)
    title_limit = min(2, max(0, int((height - line_height - gap) / title_line_height))) if title else 0
    if title and title_limit < 1:
        return None
    fitted_title, title_shortened = fit_pdf_text(pdf, title, width, title_line_height, title_limit)
    title_lines = pdf_text_lines(pdf, fitted_title, width, title_line_height) if fitted_title else []
    title_height = len(title_lines) * title_line_height + gap
    pdf.set_font(font_family, '', font_size)
    check_pdf_glyphs(pdf, text)
    max_lines = max(0, int((height - title_height + 1e-8) / line_height))
    if max_lines < 1:
        return None
    body_lines = pdf_text_lines(pdf, text, width, line_height)
    return {'font_size': font_size, 'line_height': line_height, 'title_line_height': title_line_height,
            'title': fitted_title, 'title_shortened': title_shortened, 'title_height': title_height,
            'max_lines': max_lines, 'body_lines': len(body_lines), 'text': text}


def narrative_measurement(text, block, settings=None, title=None):
    settings = settings or {}
    family = settings.get('font', 'Arial').lower()
    family = 'helvetica' if family == 'arial' else family
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(False)
    width, height = float(block.get('w') or 0), float(block.get('h') or 0)
    size, line_height = settings.get('font_size', 10) or 10, settings.get('line_height', 5) or 5
    if width <= 2 * pdf.c_margin or height <= 0:
        return {'max_lines': 0, 'body_lines': 0, 'chars_per_line': 1}
    try:
        layout = _pdf_text_layout(pdf, safe_pdf_text(text), safe_pdf_text(title or 'Resumo'), width, height, family, size, line_height)
        pdf.set_font(pdf_font_family(family), '', size)
        sample = 'A receita total do período representa os resultados da análise. '
        average_width = pdf.get_string_width(sample) / len(sample)
        return {**(layout or {'max_lines': 0, 'body_lines': 0}),
                'chars_per_line': max(1, int((width - 2 * pdf.c_margin) / average_width))}
    finally:
        for font in pdf.fonts.values():
            font.close()


def render_pdf_text_block(pdf, text, x, y, w, h, title=None, *, font_family='helvetica',
                          font_size=10, line_height=5, alignment='J', warn=None):
    text, title = safe_pdf_text(text), safe_pdf_text(title)
    configure_pdf_fonts(pdf, (font_family.lower().replace('arial', 'helvetica').removeprefix('report_'),))
    font_family = pdf_font_family(font_family)
    if w <= 2 * pdf.c_margin + font_size * 25.4 / 72 or h <= 0:
        raise TextBoxTooSmallError('Text slot is too small; enlarge the component')
    minimum = min(font_size, max(7, font_size * 0.85))
    layout = None
    for size in [font_size, (font_size + minimum) / 2, minimum]:
        layout = _pdf_text_layout(pdf, text, title, w, h, font_family, size, line_height * size / font_size)
        if layout and layout['body_lines'] <= layout['max_lines']:
            break
    if layout is None:
        raise TextBoxTooSmallError('Text slot is too small for its heading and body; enlarge the component')
    pdf.set_font(font_family, '', layout['font_size'])
    fitted, shortened = fit_pdf_text(pdf, text, w, layout['line_height'], layout['max_lines'], sentences=True)
    if warn and (shortened or layout['title_shortened']):
        warn('Narrative text or heading was shortened with an ellipsis to fit the layout slot')
    # Fitting controls layout; clipping is a final boundary for font glyph overhangs.
    with pdf.rect_clip(x, y, w, h):
        if layout['title']:
            pdf.set_xy(x, y)
            pdf.set_font(font_family, 'B', layout['font_size'])
            pdf.multi_cell(w, layout['title_line_height'], layout['title'], align='L', new_x=XPos.LEFT, new_y=YPos.NEXT)
        pdf.set_xy(x, y + layout['title_height'])
        pdf.set_font(font_family, '', layout['font_size'])
        if fitted:
            pdf.multi_cell(w, layout['line_height'], fitted, align=alignment, new_x=XPos.LEFT, new_y=YPos.NEXT)
    return {**layout, 'text': fitted, 'shortened': shortened}


def write_pdf_report_title(pdf, title, font_family='helvetica', warn=None):
    configure_pdf_fonts(pdf, (font_family.lower().replace('arial', 'helvetica').removeprefix('report_'),))
    font_family = pdf_font_family(font_family)
    text = ' '.join(safe_pdf_text(title or 'Relatório Analítico').split())
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for size in range(16, 10, -1):
        pdf.set_font(font_family, 'B', size)
        check_pdf_glyphs(pdf, text)
        if len(pdf_text_lines(pdf, text, width, 8)) <= 2:
            break
    fitted, shortened = fit_pdf_text(pdf, text, width, 8, 2)
    if shortened and warn:
        warn('Report title was shortened with an ellipsis to fit the page header')
    pdf.multi_cell(width, 8, fitted, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
