import re
from collections import Counter


VISUAL_TYPES = {"graphic", "bar", "line", "pie", "scatter", "table", "chart"}
ALLOWED_TYPES = VISUAL_TYPES | {"metric", "text"}


def safe_artifact_id(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"kpi_{text}"
    return text


def metadata_columns(metadata_items: list) -> dict:
    available = {}
    for item in metadata_items or []:
        filename = item.get("filename", "")
        for column in item.get("columns", []) or []:
            name = column.get("name")
            if not name:
                continue
            available.setdefault(filename, set()).add(str(name))
            available.setdefault("*", set()).add(str(name))
    return available


def column_exists(reference: str, available: dict) -> bool:
    text = str(reference or "")
    if ":" in text:
        filename, column = text.split(":", 1)
        if column in available.get(filename, set()):
            return True
        if column in available.get("*", set()):
            return True
    return text in available.get("*", set())


def column_profile(reference: str, metadata_items: list) -> dict | None:
    text = str(reference or "")
    filename = None
    column_name = text
    if ":" in text:
        filename, column_name = text.split(":", 1)

    for item in metadata_items or []:
        if filename and item.get("filename") != filename:
            continue
        for column in item.get("columns", []) or []:
            if column.get("name") == column_name:
                return column
    return None


def validate_report_plan(artifacts: list, metadata_items: list, template_context: dict = None) -> tuple[list, list]:
    issues = []
    if not isinstance(artifacts, list):
        return [], [{"severity": "error", "code": "invalid_plan", "message": "Artifact plan is not a list"}]

    available = metadata_columns(metadata_items)
    ids = Counter()
    validated = []

    for index, raw_artifact in enumerate(artifacts):
        if not isinstance(raw_artifact, dict):
            issues.append({"severity": "error", "code": "invalid_artifact", "message": f"Artifact {index} is not an object"})
            continue

        artifact = dict(raw_artifact)
        artifact_id = safe_artifact_id(artifact.get("id"), f"kpi_{index + 1}")
        ids[artifact_id] += 1
        if ids[artifact_id] > 1:
            new_id = f"{artifact_id}_{ids[artifact_id]}"
            issues.append({"severity": "warning", "code": "duplicate_id", "id": artifact_id, "message": f"Renamed duplicate artifact id to {new_id}"})
            artifact_id = new_id
        artifact["id"] = artifact_id

        artifact_type = str(artifact.get("type") or "graphic").lower()
        if artifact_type not in ALLOWED_TYPES:
            issues.append({"severity": "warning", "code": "invalid_type", "id": artifact_id, "message": f"Normalized unsupported type '{artifact_type}' to graphic"})
            artifact_type = "graphic"
        artifact["type"] = artifact_type

        columns = [str(column) for column in artifact.get("columns", []) or [] if str(column).strip()]
        missing_columns = [column for column in columns if not column_exists(column, available)]
        if missing_columns:
            issues.append({
                "severity": "warning",
                "code": "missing_columns",
                "id": artifact_id,
                "columns": missing_columns,
                "message": "Artifact references columns that were not found in metadata",
            })
        artifact["columns"] = columns

        sub_type = str(artifact.get("sub_type") or artifact_type).lower()
        if artifact_type in {"graphic", "pie"} and sub_type == "pie":
            high_cardinality = [
                column for column in columns
                if (column_profile(column, metadata_items) or {}).get("unique_values", 0) > 15
            ]
            if high_cardinality:
                artifact["sub_type"] = "bar"
                if artifact_type == "pie":
                    artifact["type"] = "bar"
                issues.append({
                    "severity": "warning",
                    "code": "pie_high_cardinality",
                    "id": artifact_id,
                    "columns": high_cardinality,
                    "message": "Changed pie chart to bar chart because a referenced column has high cardinality",
                })

        if not artifact.get("indicator"):
            artifact["indicator"] = artifact_id.replace("_", " ").title()
            issues.append({"severity": "warning", "code": "missing_indicator", "id": artifact_id, "message": "Generated fallback indicator name"})

        validated.append(artifact)

    if template_context:
        text_limit = int(template_context.get("text_slots") or 0)
        graphic_limit = int(template_context.get("graphic_slots") or 0)
        text_count = sum(1 for item in validated if item.get("type") == "text")
        graphic_count = sum(1 for item in validated if item.get("type") in VISUAL_TYPES)
        if text_limit and text_count > text_limit:
            issues.append({"severity": "info", "code": "text_template_limit", "message": f"Plan has {text_count} text artifacts for {text_limit} text slots"})
        if graphic_limit and graphic_count > graphic_limit:
            issues.append({"severity": "info", "code": "graphic_template_limit", "message": f"Plan has {graphic_count} visual artifacts for {graphic_limit} graphic slots"})

    return validated, issues
