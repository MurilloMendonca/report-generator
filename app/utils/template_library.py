import json
import os
import uuid

from app.utils.layout import (
    CONTENT_TOP_MM,
    DEFAULT_MARGIN_MM,
    PAGE_HEIGHT_MM,
    PAGE_WIDTH_MM,
    fallback_template,
)

TEMPLATE_LIBRARY_PATH = "workspace/layout_templates.json"
GRID_MM = 5

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
                "name": slot.get("id") or f"Slot {index + 1}",
                "description": "",
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


def _ensure_library_file():
    os.makedirs(os.path.dirname(TEMPLATE_LIBRARY_PATH), exist_ok=True)
    if not os.path.exists(TEMPLATE_LIBRARY_PATH):
        save_layout_templates(_default_templates())


def load_layout_templates() -> list:
    _ensure_library_file()
    with open(TEMPLATE_LIBRARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_layout_templates(templates: list):
    os.makedirs(os.path.dirname(TEMPLATE_LIBRARY_PATH), exist_ok=True)
    with open(TEMPLATE_LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(templates, f, indent=2, ensure_ascii=False)


def get_layout_template(template_id: str) -> dict:
    return next((t for t in load_layout_templates() if t.get("id") == template_id), None)


def default_layout_template_id() -> str:
    templates = load_layout_templates()
    return templates[0]["id"] if templates else None


def upsert_layout_template(template_data: dict) -> dict:
    templates = load_layout_templates()
    template_id = template_data.get("id") or f"tpl_{uuid.uuid4().hex[:10]}"
    template = {
        "id": template_id,
        "name": template_data.get("name") or "Novo template",
        "description": template_data.get("description", ""),
        "slots": sanitize_template_slots(template_data.get("slots", [])),
    }

    existing_index = next((i for i, item in enumerate(templates) if item.get("id") == template_id), None)
    if existing_index is None:
        templates.append(template)
    else:
        templates[existing_index] = template
    save_layout_templates(templates)
    return template


def delete_layout_template(template_id: str) -> bool:
    templates = load_layout_templates()
    kept = [template for template in templates if template.get("id") != template_id]
    if len(kept) == len(templates):
        return False
    save_layout_templates(kept)
    return True


def sanitize_template_slots(slots: list) -> list:
    sanitized = []
    for index, raw_slot in enumerate(slots or []):
        slot_type = raw_slot.get("type") if raw_slot.get("type") in {"text", "graphic"} else "graphic"
        page = max(1, int(raw_slot.get("page") or 1))
        y = max(CONTENT_TOP_MM, _snap_mm(raw_slot.get("y", CONTENT_TOP_MM)))
        h = max(10, _snap_mm(raw_slot.get("h", 40)))
        if y >= PAGE_HEIGHT_MM:
            page += y // PAGE_HEIGHT_MM
            y = y % PAGE_HEIGHT_MM

        sanitized.append({
            "slot_id": raw_slot.get("slot_id") or f"slot_{index + 1}",
            "name": raw_slot.get("name", ""),
            "description": raw_slot.get("description", ""),
            "type": slot_type,
            "page": page,
            "x": max(0, _snap_mm(raw_slot.get("x", DEFAULT_MARGIN_MM))),
            "y": y,
            "w": max(10, min(PAGE_WIDTH_MM, _snap_mm(raw_slot.get("w", 90)))),
            "h": h,
        })
    return sanitized


def select_layout_template(artifacts: list, template_id: str = None) -> dict:
    templates = load_layout_templates()
    if not templates:
        return None
    selected = get_layout_template(template_id) if template_id else None
    if selected:
        return selected
    return max(templates, key=lambda t: _template_score(t, artifacts))


def build_layout_from_template(artifacts: list, template_id: str = None) -> list:
    templates = load_layout_templates()
    if not templates:
        return fallback_template(artifacts)

    selected = select_layout_template(artifacts, template_id)

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
