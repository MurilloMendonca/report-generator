import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from app.models.db import LayoutTemplate

from app.utils.layout import (
    CONTENT_BOTTOM_MM,
    CONTENT_TOP_MM,
    DEFAULT_MARGIN_MM,
    PAGE_HEIGHT_MM,
    PAGE_WIDTH_MM,
    fallback_template,
)

# Where templates lived before Postgres; read only by `app.cli import-legacy`.
LEGACY_TEMPLATE_LIBRARY_PATH = "workspace/layout_templates.json"
GRID_MM = 1

BLUEPRINTS = {
    "executive_standard": {
        "name": "Padrão Executivo",
        "description": "Resumo no topo, 2 colunas de gráficos no meio, conclusão no rodapé.",
        "slots": [
            {"id": "kpi_resumo", "x": 10, "y": 30, "w": 190, "h": 40},
            {"type": "graphic", "x": 10, "y": 75, "w": 90, "h": 60},
            {"type": "graphic", "x": 110, "y": 75, "w": 90, "h": 60},
            {"type": "graphic", "x": 10, "y": 140, "w": 90, "h": 60},
            {"type": "graphic", "x": 110, "y": 140, "w": 90, "h": 60},
            {"id": "kpi_conclusao", "x": 10, "y": 235, "w": 190, "h": 50}
        ]
    },
    "data_heavy": {
        "name": "Dashboard Denso",
        "description": "Focado em múltiplas visualizações lado a lado.",
        "slots": [
            {"id": "kpi_resumo", "x": 10, "y": 30, "w": 190, "h": 30},
            {"type": "graphic", "x": 10, "y": 65, "w": 60, "h": 50},
            {"type": "graphic", "x": 75, "y": 65, "w": 60, "h": 50},
            {"type": "graphic", "x": 140, "y": 65, "w": 60, "h": 50},
            {"type": "graphic", "x": 10, "y": 120, "w": 190, "h": 70},
            {"type": "graphic", "x": 10, "y": 195, "w": 90, "h": 50},
            {"type": "graphic", "x": 110, "y": 195, "w": 90, "h": 50},
            {"id": "kpi_conclusao", "x": 10, "y": 250, "w": 190, "h": 35}
        ]
    },
    "document_text_only": {
        "name": "Relatório documental apenas texto",
        "description": "Template narrativo para entradas compostas só por PDFs ou documentos sem dados tabulares.",
        "slots": [
            {
                "id": "kpi_resumo",
                "name": "Resumo executivo",
                "description": "Contexto, objetivo e mensagem principal do documento.",
                "type": "text",
                "x": 10,
                "y": 30,
                "w": 190,
                "h": 55,
            },
            {
                "id": "document_key_findings",
                "name": "Principais achados",
                "description": "Achados sustentados pelo texto do PDF, citando métricas documentais quando disponíveis.",
                "type": "text",
                "x": 10,
                "y": 95,
                "w": 190,
                "h": 75,
            },
            {
                "id": "document_implications",
                "name": "Implicações e ressalvas",
                "description": "Interpretação, riscos, limitações de extração e pontos de atenção.",
                "type": "text",
                "x": 10,
                "y": 180,
                "w": 190,
                "h": 55,
            },
            {
                "id": "kpi_conclusao",
                "name": "Conclusão",
                "description": "Síntese final e recomendações baseadas no documento.",
                "type": "text",
                "x": 10,
                "y": 245,
                "w": 190,
                "h": 40,
            },
        ],
    }
}


def _slot_type(slot: dict) -> str:
    slot_type = slot.get("type")
    if slot_type in {"text", "graphic"}:
        return slot_type
    return "text" if slot.get("id") else "graphic"


def _default_templates() -> list:
    templates = []
    for template_id, blueprint in BLUEPRINTS.items():
        slots = []
        for index, slot in enumerate(blueprint["slots"]):
            slots.append({
                "slot_id": slot.get("id") or f"{template_id}_slot_{index + 1}",
                "name": slot.get("name") or slot.get("id") or f"Slot {index + 1}",
                "description": slot.get("description", ""),
                "type": _slot_type(slot),
                "page": slot.get("page", 1),
                "x": slot["x"],
                "y": slot["y"],
                "w": slot["w"],
                "h": slot["h"],
            })
        templates.append({
            "id": template_id,
            "name": blueprint["name"],
            "description": blueprint["description"],
            "slots": slots,
        })
    return templates


def _ensure_default_templates(tx) -> None:
    """Built-in templates are re-added whenever one is missing, as the JSON library did."""
    existing = set(tx.execute(select(LayoutTemplate.id)).scalars())
    missing = [template for template in _default_templates() if template["id"] not in existing]
    if not missing:
        return
    position = tx.execute(select(func.coalesce(func.max(LayoutTemplate.position), -1))).scalar()
    for offset, template in enumerate(missing, start=1):
        tx.execute(
            pg_insert(LayoutTemplate)
            .values(id=template["id"], position=position + offset, data=template)
            .on_conflict_do_nothing(index_elements=[LayoutTemplate.id])
        )


class TemplateNotEditable(Exception):
    """Built-in templates can be read and copied, not changed or deleted."""


def _visible(owner_id):
    """Built-ins (no owner) plus the caller's own templates."""
    if owner_id is None:
        return LayoutTemplate.owner_id.is_(None)
    return or_(LayoutTemplate.owner_id.is_(None), LayoutTemplate.owner_id == owner_id)


def _payload(row: LayoutTemplate) -> dict:
    return {**row.data, "id": row.id, "builtin": row.owner_id is None}


def load_layout_templates(owner_id=None) -> list:
    """Built-in templates first, then `owner_id`'s own, each in creation order."""
    with db.transaction() as tx:
        _ensure_default_templates(tx)
        rows = tx.execute(
            select(LayoutTemplate)
            .where(_visible(owner_id))
            .order_by(LayoutTemplate.owner_id.is_not(None), LayoutTemplate.position, LayoutTemplate.id)
        ).scalars()
        return [_payload(row) for row in rows if isinstance(row.data, dict)]


def save_layout_templates(templates: list, owner_id=None):
    """Replace the built-ins (owner_id None) or one owner's templates, keeping the order. Tests only."""
    with db.transaction() as tx:
        owned = LayoutTemplate.owner_id.is_(None) if owner_id is None else LayoutTemplate.owner_id == owner_id
        tx.execute(delete(LayoutTemplate).where(owned))
        for position, template in enumerate(t for t in templates if isinstance(t, dict) and t.get("id")):
            data = {key: value for key, value in template.items() if key != "builtin"}
            tx.add(LayoutTemplate(id=str(template["id"]), owner_id=owner_id, position=position, data=data))


def get_layout_template(template_id: str, owner_id=None) -> dict:
    return next((t for t in load_layout_templates(owner_id) if t.get("id") == template_id), None)


def default_layout_template_id(owner_id=None) -> str:
    templates = load_layout_templates(owner_id)
    return templates[0]["id"] if templates else None


def upsert_layout_template(template_data: dict, owner_id, must_exist: bool = False) -> dict:
    """Create a template for `owner_id`, or update one they own.

    Raises TemplateNotEditable for a built-in and LookupError for someone
    else's template (callers answer 404, so ids of other users stay hidden)."""
    template_id = str(template_data.get("id") or f"tpl_{uuid.uuid4().hex[:10]}")
    template = {
        "id": template_id,
        "name": template_data.get("name") or "Novo template",
        "description": template_data.get("description", ""),
        "slots": sanitize_template_slots(template_data.get("slots", [])),
    }
    template["page_count"] = max(
        min(100, int(template_data.get("page_count") or 1)),
        max((slot["page"] for slot in template["slots"]), default=1),
    )

    with db.transaction() as tx:
        _ensure_default_templates(tx)  # so nobody can claim a built-in id before it exists
        existing = tx.get(LayoutTemplate, template_id, with_for_update=True)
        if existing is not None:
            if existing.owner_id is None:
                raise TemplateNotEditable(template_id)
            if existing.owner_id != owner_id:
                raise LookupError(template_id)
            existing.data = template
            existing.updated_at = func.now()
        elif must_exist:
            raise LookupError(template_id)
        else:
            position = tx.execute(select(func.coalesce(func.max(LayoutTemplate.position), -1) + 1)).scalar()
            tx.add(LayoutTemplate(id=template_id, owner_id=owner_id, position=position, data=template))
    return {**template, "builtin": False}


def delete_layout_template(template_id: str, owner_id) -> bool:
    """Delete one of `owner_id`'s templates. Built-ins and others' templates are never deleted."""
    with db.transaction() as tx:
        return tx.execute(
            delete(LayoutTemplate).where(LayoutTemplate.id == str(template_id), LayoutTemplate.owner_id == owner_id)
        ).rowcount > 0


def sanitize_template_slots(slots: list) -> list:
    sanitized = []
    for index, raw_slot in enumerate(slots or []):
        slot_type = raw_slot.get("type") if raw_slot.get("type") in {"text", "graphic"} else "graphic"
        page = max(1, int(raw_slot.get("page") or 1))
        y = max(CONTENT_TOP_MM, _snap_mm(raw_slot.get("y", CONTENT_TOP_MM)))
        h = min(CONTENT_BOTTOM_MM - CONTENT_TOP_MM, max(10, _snap_mm(raw_slot.get("h", 40))))
        w = max(10, min(PAGE_WIDTH_MM, _snap_mm(raw_slot.get("w", 90))))
        if y >= PAGE_HEIGHT_MM:
            page += y // PAGE_HEIGHT_MM
            y = y % PAGE_HEIGHT_MM

        sanitized.append({
            "slot_id": raw_slot.get("slot_id") or f"slot_{index + 1}",
            "name": raw_slot.get("name", ""),
            "description": raw_slot.get("description", ""),
            "type": slot_type,
            "page": page,
            "x": max(0, min(PAGE_WIDTH_MM - w, _snap_mm(raw_slot.get("x", DEFAULT_MARGIN_MM)))),
            "y": max(CONTENT_TOP_MM, min(CONTENT_BOTTOM_MM - h, y)),
            "w": w,
            "h": h,
        })
    return sanitized


def select_layout_template(artifacts: list, template_id: str = None, owner_id=None) -> dict:
    templates = load_layout_templates(owner_id)
    if not templates:
        return None
    selected = next((t for t in templates if t.get("id") == template_id), None) if template_id else None
    if selected:
        return selected
    return max(templates, key=lambda t: _template_score(t, artifacts))


def build_layout_from_template(artifacts: list, template_id: str = None, owner_id=None) -> list:
    templates = load_layout_templates(owner_id)
    if not templates:
        return fallback_template(artifacts)

    selected = select_layout_template(artifacts, template_id, owner_id)

    slots = _ordered_slots(sanitize_template_slots(selected.get("slots", [])))
    assignments = []
    used_artifact_ids = set()

    text_artifacts = [a for a in artifacts if a.get("type") == "text"]
    graphic_artifacts = [a for a in artifacts if a.get("type") != "text"]

    for slot in slots:
        pool = text_artifacts if slot["type"] == "text" else graphic_artifacts
        artifact = _pick_artifact_for_slot(pool, used_artifact_ids, slot)
        if not artifact:
            continue
        used_artifact_ids.add(artifact["id"])
        assignments.append({
            "id": artifact["id"],
            "page": slot["page"],
            "x": slot["x"],
            "y": slot["y"],
            "w": slot["w"],
            "h": slot["h"],
        })

    leftovers = [artifact for artifact in artifacts if artifact.get("id") not in used_artifact_ids]
    if leftovers:
        extra = fallback_template(leftovers)
        last_page = max([item["page"] for item in assignments] or [0])
        for item in extra:
            item["page"] += last_page
        assignments.extend(extra)

    return assignments


def fit_artifacts_to_template(artifacts: list, template: dict) -> list:
    if not template:
        return artifacts

    slots = sanitize_template_slots(template.get("slots", []))
    text_limit = sum(1 for slot in slots if slot["type"] == "text")
    graphic_limit = sum(1 for slot in slots if slot["type"] == "graphic")

    metrics = [a for a in artifacts if a.get("type") == "metric"]
    text_artifacts = _prioritize_text_artifacts([a for a in artifacts if a.get("type") == "text"])
    graphic_artifacts = [a for a in artifacts if a.get("type") not in {"metric", "text"}]

    return metrics + text_artifacts[:text_limit] + graphic_artifacts[:graphic_limit]


def template_constraints(template: dict) -> dict:
    slots = sanitize_template_slots((template or {}).get("slots", []))
    return {
        "template_id": (template or {}).get("id"),
        "template_name": (template or {}).get("name"),
        "text_slots": sum(1 for slot in slots if slot["type"] == "text"),
        "graphic_slots": sum(1 for slot in slots if slot["type"] == "graphic"),
        "slots": [
            {
                "type": slot["type"],
                "name": slot.get("name", ""),
                "description": slot.get("description", ""),
            }
            for slot in _ordered_slots(slots)
        ],
    }


def _snap_mm(value, step: int = GRID_MM) -> int:
    return int(round(float(value) / step) * step)


def _prioritize_text_artifacts(artifacts: list) -> list:
    def rank(artifact: dict) -> int:
        artifact_id = artifact.get("id", "")
        if artifact_id == "kpi_resumo":
            return 0
        if artifact_id == "kpi_conclusao":
            return 2
        return 1

    return sorted(artifacts, key=rank)


def _ordered_slots(slots: list) -> list:
    return sorted(slots, key=lambda slot: (
        slot["page"],
        slot["y"],
        slot["x"],
    ))


def _pick_artifact_for_slot(pool: list, used_artifact_ids: set, slot: dict) -> dict:
    candidates = [artifact for artifact in pool if artifact.get("id") not in used_artifact_ids]
    if not candidates:
        return None

    hint = f"{slot.get('name', '')} {slot.get('description', '')}".lower()
    if not hint.strip():
        return candidates[0]

    scored = [(_artifact_slot_score(artifact, hint), index, artifact) for index, artifact in enumerate(candidates)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


def _artifact_slot_score(artifact: dict, hint: str) -> int:
    artifact_id = artifact.get("id", "").lower()
    content = " ".join([
        artifact_id,
        artifact.get("indicator", ""),
        artifact.get("reason", ""),
        " ".join(artifact.get("columns", []) or []),
    ]).lower()

    score = 0
    if any(word in hint for word in ["conclus", "final", "fechamento", "síntese", "sintese"]):
        score += 100 if "conclus" in artifact_id or "conclus" in content else -10
    if any(word in hint for word in ["intro", "introdu", "resumo", "abstract", "sumário", "sumario"]):
        score += 100 if "resumo" in artifact_id or "intro" in content or "resumo" in content else -10
    if any(word in hint for word in ["análise", "analise", "analysis", "detalh"]):
        score += 50 if "analysis" in artifact_id or "análise" in content or "analise" in content else 0

    hint_words = {word for word in hint.replace("_", " ").replace("-", " ").split() if len(word) > 3}
    score += sum(1 for word in hint_words if word in content)
    return score


def _template_score(template: dict, artifacts: list) -> int:
    slots = template.get("slots", [])
    text_slots = sum(1 for slot in slots if _slot_type(slot) == "text")
    graphic_slots = sum(1 for slot in slots if _slot_type(slot) == "graphic")
    text_count = sum(1 for artifact in artifacts if artifact.get("type") == "text")
    graphic_count = len(artifacts) - text_count
    return -abs(text_slots - text_count) - abs(graphic_slots - graphic_count)
