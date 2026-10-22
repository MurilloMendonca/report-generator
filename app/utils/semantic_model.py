from collections import defaultdict


def _dataset_metadata(files: list):
    for file_info in files or []:
        artifact = file_info.get("artifact") or {}
        filename = artifact.get("filename") or file_info.get("original_filename", "")
        sheets = artifact.get("sheets") or []
        if sheets:
            for index, sheet in enumerate(sheets):
                yield {
                    **sheet,
                    "filename": sheet.get("dataframe_key") or (filename if index == 0 else f"{filename}::{sheet['sheet_name']}"),
                }
        else:
            yield {**artifact, "filename": filename}


def build_semantic_model(files: list) -> dict:
    datasets = []
    column_index = defaultdict(list)

    for artifact in _dataset_metadata(files):
        filename = artifact["filename"]
        columns = artifact.get("columns", []) or []
        roles = {
            "measures": [],
            "dimensions": [],
            "dates": [],
            "identifiers": [],
            "percentages": [],
        }

        for column in columns:
            name = str(column.get("name", ""))
            role = column.get("role", "")
            if not name:
                continue
            normalized_name = name.strip().casefold()
            column_index[normalized_name].append({
                "file": filename,
                "column": name,
                "role": role,
                "type": column.get("type"),
                "rows": artifact.get("shape", {}).get("rows"),
                "unique_values": column.get("unique_values"),
                "null_count": column.get("null_count"),
            })

            if role == "measure":
                roles["measures"].append(name)
            elif role == "percentage":
                roles["percentages"].append(name)
                roles["measures"].append(name)
            elif role == "date":
                roles["dates"].append(name)
            elif "identifier" in role:
                roles["identifiers"].append(name)
            elif role == "dimension":
                roles["dimensions"].append(name)

        datasets.append({
            "filename": filename,
            "type": artifact.get("type", "tabular" if columns else artifact.get("type")),
            "shape": artifact.get("shape", {}),
            "roles": roles,
        })

    relationships = []
    for normalized_name, references in column_index.items():
        files_seen = {reference["file"] for reference in references}
        if len(files_seen) < 2:
            continue
        relationships.append({
            "column_name": normalized_name,
            "references": references,
            "confidence": "medium",
            "reason": "Same normalized column name appears in multiple datasets",
        })

    return {
        "datasets": datasets,
        "relationships": relationships,
        "summary": {
            "dataset_count": len(datasets),
            "relationship_candidates": len(relationships),
        },
    }
