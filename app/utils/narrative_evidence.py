"""Deterministic narrative references to calculated metrics, without model calls."""
import hashlib
import math
import re
from decimal import Decimal, InvalidOperation


REFERENCE = re.compile(r'\{\{metric:(/[^{}|]+)(?:\|(value|number:[0-6]))?\}\}')
NUMBER = re.compile(r'(?<!\w)[+-]?\d+(?:[.,\u00a0 ]\d+)*(?:\s*%|\s*p\.p\.)?')
UNAVAILABLE = {'', 'n/a', 'na', 'none', 'null', 'nan', 'unavailable'}


def text_digest(text):
    return hashlib.sha256(str(text).encode()).hexdigest()


def metric_catalog(metrics):
    """JSON pointers disambiguate nested metrics, labels, units and periods."""
    result = []
    def visit(value, path):
        if len(result) >= 1000:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {'evidence', 'code', 'error'}:
                    visit(child, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, child in enumerate(value[:100]):
                visit(child, path + '/' + str(index))
        elif value is not None and not isinstance(value, bool) and str(value).strip().lower() not in UNAVAILABLE:
            if isinstance(value, float) and not math.isfinite(value):
                return
            result.append({'pointer': path, 'value': value})
    visit(metrics or {}, '')
    return result


def _metric(metrics, pointer):
    value, parent = metrics, {}
    for segment in pointer[1:].split('/'):
        segment = segment.replace('~1', '/').replace('~0', '~')
        parent = value
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    if value is None or isinstance(value, (bool, dict, list)) or str(value).strip().lower() in UNAVAILABLE:
        raise ValueError('Unavailable metric')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite metric')
    return value, parent if isinstance(parent, dict) else {}


def expand_references(text, metrics):
    references, errors, pieces, cursor, length = [], [], [], 0, 0
    for match in REFERENCE.finditer(text):
        prefix = text[cursor:match.start()]
        pieces.append(prefix)
        length += len(prefix)
        pointer, formatting = match.group(1), match.group(2) or 'value'
        try:
            value, context = _metric(metrics, pointer)
            if formatting.startswith('number:'):
                rendered = format(Decimal(str(value)), '.' + formatting.split(':')[1] + 'f')
                if not Decimal(rendered).is_finite():
                    raise ValueError('Non-finite metric')
            else:
                rendered = str(value)
            references.append({'pointer': pointer, 'value': value, 'display': rendered, 'start': length,
                               'end': length + len(rendered), 'metric_id': pointer[1:].split('/')[0].replace('~1', '/').replace('~0', '~'),
                               **{key: context[key] for key in ('unit', 'currency', 'period', 'source') if isinstance(context.get(key), (str, int, float))}})
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
            errors.append(pointer)
            rendered = match.group(0)
        pieces.append(rendered)
        length += len(rendered)
        cursor = match.end()
    pieces.append(text[cursor:])
    expanded = ''.join(pieces)
    if '{{metric:' in REFERENCE.sub('', text):
        errors.append('invalid_reference')
    return expanded, references, errors


def ground_narrative(artifact, text, metrics):
    """Keep provenance beside the component; manual edits never inherit trust."""
    if '{{metric:' not in text:
        return text
    expanded, references, errors = expand_references(text, metrics)
    artifact['narrative_evidence'] = {'template': text, 'text_hash': text_digest(expanded), 'references': references, 'errors': errors}
    return expanded


def review_narrative(artifact, text, metrics):
    issues, references = [], []
    evidence = artifact.get('narrative_evidence') or {}
    if evidence and evidence.get('text_hash') == text_digest(text):
        expanded, references, errors = expand_references(evidence.get('template', ''), metrics)
        contexts = lambda refs: [{key: ref.get(key) for key in ('pointer', 'unit', 'currency', 'period', 'source')} for ref in refs]
        if errors or expanded != text or contexts(references) != contexts(evidence.get('references', [])):
            issues.append(('error', 'metric_reference_changed', 'Uma referência usa uma métrica ausente ou alterada. Gere o texto novamente.'))
    elif evidence:
        issues.append(('warning', 'evidence_stale', 'O texto foi editado; suas referências precisam ser revisadas.'))
    if '{{metric:' in text:
        issues.append(('error', 'invalid_metric_reference', 'Há uma referência de métrica inválida ou indisponível no texto.'))
    remaining = list(text)
    for reference in references:
        remaining[reference['start']:reference['end']] = ' ' * (reference['end'] - reference['start'])
        around = text[max(0, reference['start'] - 5):reference['end'] + 5]
        unit = str(reference.get('currency') or reference.get('unit') or '').upper()
        currency = {'EUR': '€', 'GBP': '£', 'USD': '$', 'BRL': 'R$'}.get(unit)
        if currency and any(symbol in around for symbol in ('€', '£', '$')) and currency not in around:
            issues.append(('error', 'metric_unit_mismatch', 'A moeda no texto difere da unidade da métrica referenciada.'))
        if unit in {'EUR', 'GBP', 'USD', 'BRL', 'COUNT', 'UNITS'} and '%' in around:
            issues.append(('error', 'metric_unit_mismatch', 'Uma métrica absoluta foi apresentada como porcentagem.'))
        if unit in {'RATIO', 'FRACTION', 'PERCENTAGE_POINTS', 'PP', 'P.P.'} and '%' in around:
            issues.append(('error', 'metric_unit_mismatch', 'A unidade percentual não corresponde à escala da métrica. Calcule a porcentagem ou mantenha a unidade original.'))
        if not unit and any(symbol in around for symbol in ('€', '£', '$', '%', 'p.p.')):
            issues.append(('warning', 'metric_unit_unverified', 'O valor está referenciado, mas a métrica não informa uma unidade verificável. Confira a moeda ou escala percentual.'))
    if NUMBER.search(''.join(remaining)):
        issues.append(('warning', 'unreferenced_numbers', 'Há números ou períodos sem referência verificável. Confira valores, unidades e datas nos dados.'))
    return issues, references
