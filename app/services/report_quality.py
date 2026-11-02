"""Review a candidate PDF and its evidence before publishing it to the workspace."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image
from pypdf import PdfReader

from app.utils.layout import normalize_template
from app.utils.narrative_evidence import review_narrative


# The sandbox renderer records English messages in its manifest; the review shows
# them to users, so translate the known ones here. Unknown text passes through.
_RENDER_MESSAGES = {
    'Table rows or columns were limited to fit the layout slot':
        'Linhas ou colunas da tabela foram limitadas para caber no espaço do componente.',
    'Table text was shortened with ellipses to fit the layout slot':
        'Textos da tabela foram encurtados com reticências para caber no espaço do componente.',
    'Table title was shortened with an ellipsis to fit the layout slot':
        'O título da tabela foi encurtado com reticências para caber no espaço do componente.',
    'Long chart labels were shortened with ellipses to fit the layout slot':
        'Rótulos longos do gráfico foram encurtados com reticências para caber no espaço do componente.',
    'Long legend labels were shortened with ellipses to fit the layout slot':
        'Rótulos longos da legenda foram encurtados com reticências para caber no espaço do componente.',
    'Chart title was shortened with an ellipsis to fit the layout slot':
        'O título do gráfico foi encurtado com reticências para caber no espaço do componente.',
    'Some point/value labels were omitted to avoid overlap; plotted data is unchanged':
        'Alguns rótulos de pontos ou valores foram omitidos para evitar sobreposição; os dados do gráfico não mudaram.',
    'Some axis tick labels were omitted to avoid overlap; plotted data is unchanged':
        'Alguns rótulos dos eixos foram omitidos para evitar sobreposição; os dados do gráfico não mudaram.',
    'Some rows had no matching record in a joined table':
        'Algumas linhas não encontraram correspondência na tabela relacionada; os campos dela ficaram vazios nessas linhas.',
    'Scatter points were sampled deterministically':
        'O gráfico de dispersão mostra uma amostra determinística dos pontos.',
    'Minor scatter categories were grouped as Others; all sampled points are shown':
        'Categorias pequenas do gráfico de dispersão foram agrupadas em Outros; todos os pontos amostrados aparecem.',
    'Chart slot is too small; enlarge it or select fewer categories/series':
        'O espaço do gráfico é pequeno demais. Aumente o bloco ou selecione menos categorias ou séries.',
    'Table slot is too small; enlarge it or select fewer columns':
        'O espaço da tabela é pequeno demais. Aumente o bloco ou selecione menos colunas.',
    'Text slot is too small; enlarge the component':
        'O espaço do texto é pequeno demais. Aumente o componente.',
    'Text slot is too small for its heading and body; enlarge the component':
        'O espaço é pequeno demais para o título e o texto. Aumente o componente.',
}
_RENDER_PREFIXES = (
    ('Chart/table text has no visible pixels: ', 'Um texto do gráfico ou da tabela ficou invisível no PDF: '),
    ('Chart font cannot render: ', 'A fonte do gráfico não tem os caracteres: '),
    ('Font cannot render: ', 'A fonte do PDF não tem os caracteres: '),
)


def _render_message(message):
    message = str(message)
    if message in _RENDER_MESSAGES:
        return _RENDER_MESSAGES[message]
    for prefix, translated in _RENDER_PREFIXES:
        if message.startswith(prefix):
            return translated + message[len(prefix):]
    return message


def _literal_text(code):
    try:
        tree = ast.parse(code or '')
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'render_text_block':
            value = node.args[1] if len(node.args) > 1 else next((kw.value for kw in node.keywords if kw.arg == 'text'), None)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


def _normalized(text):
    return re.sub(r'\s+', '', str(text))


def review_render_failure(session, manifest):
    """Renderer checks can fail before there is a candidate PDF to inspect."""
    issues = [{'component_id': item.get('id'), 'severity': 'error',
               'code': item.get('code') or 'render_failed',
               'message': _render_message(item.get('error') or item.get('message') or 'O componente não foi renderizado.')[:500]}
              for item in manifest.get('components', []) if item.get('status') != 'rendered']
    return _finish({'checked_at': datetime.now(timezone.utc).isoformat(), 'candidate_version': session.get('pdf_version', 0) + 1,
                    'published_version': session.get('pdf_version', 0), 'issues': issues, 'components': [],
                    'checks': ['font_glyphs', 'chart_text_pixels']})


def review_report(session, pdf_path, manifest):
    """Errors block publication; uncertain evidence produces actionable warnings.

    The check verifies presence and supported glyphs, not factual causality or
    the meaning of arbitrary custom graphics. Those limits are visible in UI.
    """
    review = {'status': 'passed', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'candidate_version': session.get('pdf_version', 0) + 1,
              'published_version': session.get('pdf_version', 0), 'issues': [], 'components': [],
              'checks': ['metric_references', 'component_manifest', 'pdf_text', 'pdf_pixels', 'font_glyphs']}
    def issue(component_id, severity, code, message):
        review['issues'].append({'component_id': component_id, 'severity': severity, 'code': code, 'message': message})

    artifacts = {item['id']: item for item in session.get('artifacts', []) if item.get('id')}
    template = normalize_template(session.get('template', []))
    outcomes = {item.get('id'): item for item in manifest.get('components', [])}
    for artifact_id, artifact in artifacts.items():
        if artifact.get('type') == 'metric':
            continue
        if not any(slot.get('id') == artifact_id for slot in template):
            issue(artifact_id, 'warning', 'not_in_layout', 'O componente não está incluído no layout do PDF.')
    try:
        path = Path(pdf_path)
        if not path.is_file() or path.stat().st_size > 200 * 1024 * 1024:
            raise ValueError('PDF ausente ou acima do limite de revisão.')
        reader = PdfReader(path)
        if not 1 <= len(reader.pages) <= 50:
            raise ValueError('A revisão aceita PDFs com 1 a 50 páginas.')
        extracted = [page.extract_text() or '' for page in reader.pages]
        review['page_count'] = len(reader.pages)
        requested_pages = session.get('settings', {}).get('page_count')
        if requested_pages is not None and len(reader.pages) != requested_pages:
            issue(None, 'error', 'page_count_mismatch', f'O relatório deve ter {requested_pages} páginas; o PDF gerado tem {len(reader.pages)}.')
        if any('\ufffd' in text for text in extracted):
            issue(None, 'error', 'replacement_character', 'O PDF contém caracteres substituídos por falha de codificação.')
    except Exception:
        issue(None, 'error', 'invalid_pdf', 'O PDF não pôde ser lido para revisão ou excede o limite de 50 páginas / 200 MB.')
        return _finish(review)

    for slot in template:
        component_id = slot['id']
        artifact = artifacts.get(component_id, {})
        outcome = outcomes.get(component_id)
        page = int(slot.get('page', 1))
        component = {'id': component_id, 'page': page, 'references': [],
                     'pixel_text_count': (outcome or {}).get('pixel_text_count', 0)}
        review['components'].append(component)
        if not outcome or outcome.get('status') != 'rendered':
            issue(component_id, 'error', 'missing_component', 'O componente não foi renderizado no PDF.')
            continue
        if page < 1 or page > len(reader.pages):
            issue(component_id, 'error', 'missing_page', 'A página do componente não existe no PDF.')
            continue
        for warning in outcome.get('warnings', []):
            issue(component_id, 'warning', 'render_warning', _render_message(warning)[:500])
        if not outcome.get('glyph_check'):
            issue(component_id, 'warning', 'custom_output_unverified', 'O código personalizado não usou os renderizadores com verificação de glifos e texto. Revise sua aparência no PDF.')
        for block in outcome.get('text_blocks', []):
            expected = _normalized(str(block.get('title', '')) + str(block.get('text', '')))
            if expected and expected not in _normalized(extracted[page - 1]):
                issue(component_id, 'error', 'pdf_text_missing', 'Parte do texto esperado não foi encontrada no PDF exportado.')
        if artifact.get('type') == 'text':
            text = _literal_text(session.get('components', {}).get(component_id))
            if text is None:
                issue(component_id, 'warning', 'dynamic_narrative', 'O texto é calculado por código; confira seus números e fontes manualmente.')
            else:
                findings, references = review_narrative(artifact, text, session.get('calculated_metrics', {}))
                component['references'] = references
                for severity, code, message in findings:
                    issue(component_id, severity, code, message)

    if not shutil.which('pdftoppm'):
        issue(None, 'error', 'pixel_check_unavailable', 'Instale poppler-utils para verificar os pixels do PDF antes de publicar.')
        return _finish(review)
    try:
        with tempfile.TemporaryDirectory(prefix='report-quality-') as directory:
            for number, page in enumerate(reader.pages, 1):
                prefix = str(Path(directory) / 'page')
                subprocess.run(['pdftoppm', '-f', str(number), '-l', str(number), '-singlefile', '-scale-to', '1400',
                                '-gray', '-png', str(path.absolute()), prefix], check=True, timeout=20,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with Image.open(prefix + '.png') as image:
                    pixels = np.asarray(image.convert('L'))
                height, width = pixels.shape
                page_width, page_height = float(page.mediabox.width) * 25.4 / 72, float(page.mediabox.height) * 25.4 / 72
                for slot in template:
                    if int(slot.get('page', 1)) != number:
                        continue
                    x, y, w, h = (float(slot.get(key, 0)) for key in ('x', 'y', 'w', 'h'))
                    if min(x, y) < 0 or min(w, h) <= 0 or x + w > page_width + .5 or y + h > page_height + .5:
                        issue(slot['id'], 'error', 'component_outside_page', 'O componente ultrapassa os limites da página.')
                        continue
                    crop = pixels[max(0, int(y / page_height * height)):min(height, int((y + h) / page_height * height)),
                                  max(0, int(x / page_width * width)):min(width, int((x + w) / page_width * width))]
                    if crop.size == 0 or np.count_nonzero(crop < 245) < 5:
                        issue(slot['id'], 'error', 'blank_component', 'O espaço do componente está vazio no PDF exportado.')
    except (OSError, subprocess.SubprocessError, ValueError):
        issue(None, 'error', 'pixel_check_failed', 'Não foi possível concluir a verificação visual do PDF. Tente renderizar novamente.')
    return _finish(review)


def _finish(review):
    review['error_count'] = sum(item['severity'] == 'error' for item in review['issues'])
    review['warning_count'] = sum(item['severity'] == 'warning' for item in review['issues'])
    review['status'] = 'failed' if review['error_count'] else 'warning' if review['warning_count'] else 'passed'
    return review
