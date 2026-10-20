import re
from collections import Counter
from pydantic import ValidationError

from app.utils.component_library import (
    get_preset,
    infer_preset_id,
    normalize_visual_options,
)
from app.utils.component_renderers import supports_library_renderer
from app.utils.tabular import metadata_tables
from app.utils.visual_contracts import DataBinding

VISUAL_TYPES = {"graphic", "bar", "line", "pie", "scatter", "table", "chart", "radial", "donut", "gauge"}
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
    for item in metadata_tables(metadata_items):
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
    filename, column = split_column_reference(text)
    if filename:
        return column in available.get(filename, set())
    return text in available.get("*", set())


def column_profile(reference: str, metadata_items: list) -> dict | None:
    text = str(reference or "")
    filename, column_name = split_column_reference(text)

    for item in metadata_tables(metadata_items):
        if filename and item.get("filename") != filename:
            continue
        for column in item.get("columns", []) or []:
            if column.get("name") == column_name:
                return column
    return None


def split_column_reference(reference: str) -> tuple[str | None, str]:
    text = str(reference or "").strip()
    parts = re.split(r"(?<!:):(?!:)", text, maxsplit=1)
    if len(parts) == 1:
        return None, text
    filename, column = parts
    return filename or None, column


def metadata_sources(metadata_items: list) -> set[str]:
    return {
        str(item.get("filename"))
        for item in metadata_tables(metadata_items)
        if item.get("filename")
    }


def column_kind(profile: dict | None) -> str:
    if not profile:
        return "unknown"
    role = str(profile.get("role") or "").lower()
    name = str(profile.get("name") or "").strip().lower()
    if role == "date":
        return "temporal"
    raw_type = str(profile.get("type") or profile.get("dtype") or "").lower()
    if any(token in raw_type for token in ["date", "time", "datetime"]):
        return "temporal"
    if name in {"year", "ano", "año"}:
        stats = profile.get("stats") or {}
        try:
            min_value = float(stats.get("min"))
            max_value = float(stats.get("max"))
        except (TypeError, ValueError):
            min_value = max_value = None
        if min_value is not None and 1000 <= min_value <= max_value <= 3000:
            return "temporal"
    if any(token in raw_type for token in ["int", "float", "number", "numeric", "decimal"]):
        return "numeric"
    return "categorical"


def role_accepts_kind(expected: str | None, actual: str) -> bool:
    if expected in {"any", None}:
        return True
    if expected == "categorical":
        return actual in {"categorical", "temporal", "unknown"}
    if expected == "numeric":
        return actual in {"numeric", "unknown"}
    if expected == "temporal":
        return actual in {"temporal", "unknown"}
    return True


def role_accepts_binding(role: dict, actual: str, aggregation: str | None = None) -> bool:
    role_name = str(role.get("name") or "")
    expected = role.get("kind")
    if (
        expected == "numeric"
        and str(aggregation or "").lower() == "count"
        and role_name in {"value", "metrics"}
    ):
        return actual in {"categorical", "numeric", "temporal", "unknown"}
    return role_accepts_kind(expected, actual)


def role_candidate_columns(columns: list[str], role: dict, metadata_items: list) -> list[str]:
    expected = role.get("kind")
    candidates = []
    fallback = []
    for reference in columns:
        profile = column_profile(reference, metadata_items)
        kind = column_kind(profile)
        if role_accepts_kind(expected, kind):
            candidates.append(reference)
        else:
            fallback.append(reference)
    return candidates or fallback


def normalize_binding_column(value: str, source: str | None = None) -> str:
    filename, column = split_column_reference(value)
    return value if filename and source and filename != source else column


def binding_reference(source: str | None, column: str) -> str:
    if source and split_column_reference(column)[0] is None:
        return f"{source}:{column}"
    return str(column)


def infer_data_binding(artifact: dict, preset: dict, metadata_items: list) -> dict:
    columns = artifact.get("columns", []) or []
    first_source = None
    for reference in columns:
        source, _column = split_column_reference(reference)
        if source:
            first_source = source
            break

    roles = {}
    used = set()
    aggregation = (artifact.get("data_binding") or {}).get("aggregation") or (artifact.get("visual_options") or {}).get("aggregation")
    for role in preset.get("data_roles", []):
        candidates = [item for item in columns if item not in used and role_accepts_binding(
            role, column_kind(column_profile(item, metadata_items)), aggregation
        )]
        if not candidates and role.get("required"):
            candidates = role_candidate_columns(columns, role, metadata_items)
        candidate = next((item for item in candidates if item not in used), None)
        if not candidate:
            continue
        chosen = [item for item in candidates if item not in used] if role.get("multiple") else [candidate]
        used.update(chosen)
        values = [normalize_binding_column(item, first_source) for item in chosen]
        roles[role["name"]] = values if len(values) > 1 else values[0]

    options = artifact.get("visual_options") or {}
    return {
        "source": first_source,
        "roles": roles,
        "aggregation": options.get("aggregation") or "sum",
        "sort": options.get("sort") or (("desc" if options.get("sort_desc", True) else "asc") if preset.get("family") in {"bar", "table"} else "none"),
        "filters": [],
        "limit": options.get("limit"),
    }


def normalize_data_binding(artifact: dict, preset: dict, metadata_items: list, issues: list) -> dict:
    inferred_binding = infer_data_binding(artifact, preset, metadata_items)
    binding = artifact.get("data_binding")
    if not isinstance(binding, dict):
        binding = inferred_binding
    else:
        binding = dict(binding)
        if not isinstance(binding.get("roles", {}), dict):
            issues.append({"severity": "warning", "code": "invalid_data_binding", "id": artifact.get("id"),
                           "field": "roles", "message": "Data binding roles must be an object"})
        binding["roles"] = dict(binding.get("roles") or {}) if isinstance(binding.get("roles", {}), dict) else {}
        binding.setdefault("sort", inferred_binding["sort"])
        binding.setdefault("aggregation", inferred_binding["aggregation"])
        inferred_roles = inferred_binding.get("roles") or {}
        for role in preset.get("data_roles", []):
            role_name = role.get("name")
            if role_name and not binding["roles"].get(role_name) and inferred_roles.get(role_name):
                binding["roles"][role_name] = inferred_roles[role_name]
        if not binding.get("source") and inferred_binding.get("source"):
            binding["source"] = inferred_binding["source"]

    sources = metadata_sources(metadata_items)
    source = binding.get("source")
    if source and source not in sources:
        issues.append({
            "severity": "warning",
            "code": "data_binding_source_missing",
            "id": artifact.get("id"),
            "source": source,
            "message": "Data binding source was not found in metadata",
        })
    elif not source:
        for reference in artifact.get("columns", []) or []:
            candidate_source, _column = split_column_reference(reference)
            if candidate_source:
                source = candidate_source
                binding["source"] = source
                break
        if not source and len(sources) == 1:
            source = next(iter(sources))
            binding["source"] = source
        elif not source:
            issues.append({"severity": "warning", "code": "data_binding_source_missing", "id": artifact.get("id"),
                           "message": "Choose an explicit source for a visual with multiple datasets"})

    visual_warnings = []
    normalized_roles = {}
    roles = binding.get("roles") or {}
    for role in preset.get("data_roles", []):
        role_name = role.get("name")
        if not role_name:
            continue
        value = roles.get(role_name)
        if not value:
            if role.get("required"):
                visual_warnings.append(f"data_binding.roles.{role_name} ausente")
                issues.append({
                    "severity": "warning",
                    "code": "data_binding_role_missing",
                    "id": artifact.get("id"),
                    "preset_id": preset.get("id"),
                    "role": role_name,
                    "message": "Required data binding role is missing",
                })
            continue

        values = value if isinstance(value, list) else [value]
        normalized_values = []
        if isinstance(value, list) and not role.get("multiple"):
            issues.append({"severity": "warning", "code": "data_binding_role_mismatch", "id": artifact.get("id"),
                           "role": role_name, "message": "This visual role requires a single column"})
        for item in values:
            if not isinstance(item, str):
                issues.append({"severity": "warning", "code": "invalid_data_binding", "id": artifact.get("id"),
                               "role": role_name, "message": "Column roles must contain column names"})
                continue
            column = normalize_binding_column(item.strip(), source)
            normalized_values.append(column)
            reference = binding_reference(source, column)
            profile = column_profile(reference, metadata_items)
            kind = column_kind(profile)
            if split_column_reference(column)[0] not in {None, source} or not profile:
                visual_warnings.append(f"coluna `{reference}` não encontrada na fonte selecionada")
                issues.append({"severity": "warning", "code": "data_binding_column_missing", "id": artifact.get("id"),
                               "role": role_name, "column": reference, "message": "Column was not found in the selected source"})
            elif not role_accepts_binding(role, kind, binding.get("aggregation")):
                visual_warnings.append(f"`{reference}` não parece compatível com `{role_name}`")
                issues.append({"severity": "warning", "code": "data_binding_role_mismatch", "id": artifact.get("id"),
                               "role": role_name, "column": reference, "expected_kind": role.get("kind"),
                               "actual_kind": kind, "message": "Column has an incompatible type for this role"})
        normalized_roles[role_name] = normalized_values if isinstance(value, list) else next(iter(normalized_values), "")

    binding["roles"] = normalized_roles
    binding["aggregation"] = str(binding.get("aggregation") or "sum")
    binding["sort"] = str(binding.get("sort") or "none")
    filters = binding.get("filters")
    binding["filters"] = [] if filters is None else filters
    if binding.get("limit") == "" or binding.get("limit") is None:
        binding["limit"] = None
    try:
        binding = DataBinding.model_validate(binding).model_dump()
    except ValidationError as exc:
        for error in exc.errors():
            issues.append({"severity": "warning", "code": "invalid_data_binding", "id": artifact.get("id"),
                           "field": ".".join(map(str, error["loc"])), "message": "Invalid data binding type or value"})
    artifact["visual_warnings"] = visual_warnings
    return binding


def normalize_render_mode(artifact: dict, issues: list, preset: dict | None = None) -> str:
    eligible = supports_library_renderer((preset or {}).get("id")) and not any(
        issue.get("id") == artifact.get("id") and (
            issue.get("code", "").startswith("data_binding_") or issue.get("code") == "invalid_data_binding"
        ) for issue in issues
    )
    requested = str(artifact.get("render_mode") or ("library" if eligible else "generated_code")).lower()
    if requested not in {"generated_code", "library", "hybrid"}:
        issues.append({
            "severity": "warning",
            "code": "invalid_render_mode",
            "id": artifact.get("id"),
            "render_mode": requested,
            "message": "Normalized unknown render mode to generated_code",
        })
        return "generated_code"
    if requested in {"library", "hybrid"} and not supports_library_renderer((preset or {}).get("id")):
        issues.append({
            "severity": "warning",
            "code": "library_render_unsupported",
            "id": artifact.get("id"),
            "preset_id": (preset or {}).get("id"),
            "message": "Library render mode is not available for this preset; using generated_code",
        })
        return "generated_code"
    return requested


def validate_preset_roles(artifact: dict, preset: dict, metadata_items: list, issues: list):
    columns = artifact.get("columns", [])
    profiles = [column_profile(column, metadata_items) for column in columns]
    kinds = [column_kind(profile) for profile in profiles]
    required_roles = [
        role for role in preset.get("data_roles", [])
        if role.get("required")
    ]
    for role in required_roles:
        expected = role.get("kind")
        if expected in {"any", None}:
            continue
        if (
            expected == "numeric"
            and str((artifact.get("data_binding") or {}).get("aggregation") or "").lower() == "count"
            and role.get("name") in {"value", "metrics"}
        ):
            continue
        if expected == "categorical":
            found = any(kind in {"categorical", "temporal", "unknown"} for kind in kinds)
        elif expected == "numeric":
            found = any(kind in {"numeric", "unknown"} for kind in kinds)
        elif expected == "temporal":
            found = any(kind in {"temporal", "unknown"} for kind in kinds)
        else:
            found = True
        if not found:
            issues.append({
                "severity": "warning",
                "code": "preset_role_mismatch",
                "id": artifact.get("id"),
                "preset_id": preset.get("id"),
                "role": role.get("name"),
                "expected_kind": expected,
                "message": "Artifact columns may not satisfy the selected visual preset role",
            })


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

        if artifact_type in VISUAL_TYPES:
            preset = get_preset(artifact.get("component_preset_id"))
            if not preset:
                if artifact.get("component_preset_id"):
                    issues.append({
                        "severity": "warning",
                        "code": "invalid_component_preset",
                        "id": artifact_id,
                        "preset_id": artifact.get("component_preset_id"),
                        "message": "Normalized unknown component preset",
                    })
                artifact["component_preset_id"] = infer_preset_id(artifact)
                preset = get_preset(artifact["component_preset_id"])

            if preset and artifact_type not in preset.get("allowed_types", []):
                issues.append({
                    "severity": "warning",
                    "code": "preset_type_mismatch",
                    "id": artifact_id,
                    "preset_id": preset.get("id"),
                    "message": "Selected preset does not normally match this artifact type",
                })

            if preset:
                artifact["sub_type"] = artifact.get("sub_type") or preset.get("family")
                options = artifact.get("visual_options") or {}
                binding = artifact.get("data_binding") or {}
                if not isinstance(options, dict):
                    issues.append({"severity": "warning", "code": "invalid_visual_options", "id": artifact_id,
                                   "message": "Visual options must be an object"})
                    options = {}
                if not isinstance(binding, dict):
                    issues.append({"severity": "warning", "code": "invalid_data_binding", "id": artifact_id,
                                   "message": "Data binding must be an object"})
                    binding = {}
                options, binding = dict(options), dict(binding)
                # Older plans put calculation settings in visual_options. Move
                # them to the authoritative binding before validating options.
                for field in ("aggregation", "sort", "filters", "limit"):
                    if field in options:
                        binding.setdefault(field, options.pop(field))
                artifact["data_binding"] = binding
                artifact["visual_options"] = normalize_visual_options(preset, options, issues, artifact_id)
                artifact["data_binding"] = normalize_data_binding(artifact, preset, metadata_items, issues)
                artifact["render_mode"] = normalize_render_mode(artifact, issues, preset)
                validate_preset_roles(artifact, preset, metadata_items, issues)

        sub_type = str(artifact.get("sub_type") or artifact_type).lower()
        if artifact_type in {"graphic", "pie", "radial", "donut"} and sub_type in {"pie", "donut", "radial"}:
            high_cardinality = [
                column for column in columns
                if (column_profile(column, metadata_items) or {}).get("unique_values", 0) > 15
            ]
            if high_cardinality:
                artifact["sub_type"] = "bar"
                artifact["component_preset_id"] = "bar_ranked"
                preset = get_preset("bar_ranked")
                artifact["visual_options"] = normalize_visual_options(preset, artifact.get("visual_options"))
                artifact["data_binding"] = normalize_data_binding(artifact, preset, metadata_items, issues)
                artifact["render_mode"] = normalize_render_mode(artifact, issues, preset)
                if artifact_type in {"pie", "radial", "donut"}:
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
        if text_count > text_limit:
            issues.append({"severity": "info", "code": "text_template_limit", "message": f"Plan has {text_count} text artifacts for {text_limit} text slots; overflow will use additional pages"})
        if graphic_count > graphic_limit:
            issues.append({"severity": "info", "code": "graphic_template_limit", "message": f"Plan has {graphic_count} visual artifacts for {graphic_limit} graphic slots; overflow will use additional pages"})

    return validated, issues
