import os
import re
from collections import Counter

import numpy as np
import pandas as pd


CSV_ENCODINGS = ["utf-8", "utf-8-sig", "latin-1"]
CSV_SEPARATORS = [",", ";", "\t", "|"]
MAX_PROFILE_ROWS = 10000

def sanitize_data(obj):
    """
    Recursively replaces non-JSON compliant floats (NaN, Inf) with None.
    """
    if isinstance(obj, dict):
        return {k: sanitize_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_data(i) for i in obj]
    elif isinstance(obj, float):
        if np.isnan(obj) or np.isinf(obj):
            return None
    return obj


def read_csv_with_detection(file_path: str) -> tuple[pd.DataFrame, dict]:
    best = None
    errors = []
    for encoding in CSV_ENCODINGS:
        for separator in CSV_SEPARATORS:
            try:
                df = pd.read_csv(file_path, encoding=encoding, sep=separator, nrows=MAX_PROFILE_ROWS)
                score = (len(df.columns), -int(df.isnull().sum().sum()))
                candidate = (score, df, {"encoding": encoding, "separator": separator})
                if best is None or candidate[0] > best[0]:
                    best = candidate
            except Exception as exc:
                errors.append(f"{encoding}/{separator}: {exc}")

    if best is None:
        raise ValueError("; ".join(errors[:3]) or "CSV parsing failed")
    return best[1], best[2]


def infer_column_role(series: pd.Series) -> str:
    name = str(series.name).lower()
    non_null = series.dropna()
    if non_null.empty:
        return "empty"
    if "date" in name or "data" in name or pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if any(token in name for token in ["id", "uuid", "key", "codigo", "code"]):
        return "identifier"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        if any(token in name for token in ["pct", "percent", "rate", "taxa"]):
            return "percentage"
        return "measure"
    unique_count = int(non_null.nunique())
    avg_len = float(non_null.astype(str).str.len().mean())
    unique_ratio = float(unique_count / max(len(non_null), 1))
    if unique_ratio > 0.85 and unique_count > 50 and avg_len > 20:
        return "text_or_identifier"
    return "dimension"


def top_values(series: pd.Series, limit: int = 5) -> list:
    values = []
    counts = Counter(str(value) for value in series.dropna().head(MAX_PROFILE_ROWS))
    for value, count in counts.most_common(limit):
        values.append({"value": value, "count": int(count)})
    return values


def dataframe_metadata(df: pd.DataFrame, display_name: str, sheet_name: str = None, parser: dict = None) -> dict:
    rows, cols = df.shape
    columns_info = []
    for col in df.columns:
        series = df[col]
        dtype = str(series.dtype)
        null_count = int(series.isnull().sum())
        unique_count = int(series.nunique(dropna=True))

        col_info = {
            "name": str(col),
            "type": dtype,
            "role": infer_column_role(series),
            "null_count": null_count,
            "null_ratio": round(null_count / rows, 4) if rows else 0,
            "unique_values": unique_count,
            "unique_ratio": round(unique_count / rows, 4) if rows else 0,
            "top_values": top_values(series),
        }

        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            if not numeric.empty:
                col_info["stats"] = {
                    "mean": float(numeric.mean()),
                    "median": float(numeric.median()),
                    "min": float(numeric.min()),
                    "max": float(numeric.max()),
                    "std": float(numeric.std()) if len(numeric) > 1 else 0,
                    "q25": float(numeric.quantile(0.25)),
                    "q75": float(numeric.quantile(0.75)),
                }
        elif col_info["role"] == "date":
            dates = pd.to_datetime(series, errors="coerce").dropna()
            if not dates.empty:
                col_info["date_range"] = {
                    "min": dates.min().isoformat(),
                    "max": dates.max().isoformat(),
                }

        columns_info.append(col_info)

    artifact = {
        "filename": display_name,
        "shape": {"rows": int(rows), "columns": int(cols)},
        "columns": columns_info,
        "sample": df.head(8).to_dict(orient="records"),
        "profile": {
            "sampled_rows": min(int(rows), MAX_PROFILE_ROWS),
            "column_roles": {
                "measures": [c["name"] for c in columns_info if c["role"] in {"measure", "percentage"}],
                "dimensions": [c["name"] for c in columns_info if c["role"] == "dimension"],
                "dates": [c["name"] for c in columns_info if c["role"] == "date"],
                "identifiers": [c["name"] for c in columns_info if "identifier" in c["role"]],
            },
        },
    }
    if sheet_name is not None:
        artifact["sheet_name"] = sheet_name
    if parser:
        artifact["parser"] = parser
    return sanitize_data(artifact)


def extract_metadata(file_path: str, original_filename: str = None) -> dict:
    """
    Reads a CSV, Excel, or PDF file and returns metadata summarizing its structure and content.
    """
    ext = os.path.splitext(file_path)[1].lower()
    display_name = original_filename or os.path.basename(file_path)
    
    try:
        if ext == '.csv':
            df, parser = read_csv_with_detection(file_path)
            return dataframe_metadata(df, display_name, parser=parser)
        elif ext in ['.xls', '.xlsx']:
            sheets = pd.read_excel(file_path, sheet_name=None, nrows=MAX_PROFILE_ROWS)
            if len(sheets) == 1:
                sheet_name, df = next(iter(sheets.items()))
                return dataframe_metadata(df, display_name, sheet_name=sheet_name)
            sheet_artifacts = [
                dataframe_metadata(df, display_name, sheet_name=sheet_name)
                for sheet_name, df in sheets.items()
            ]
            return sanitize_data({
                "filename": display_name,
                "type": "excel_workbook",
                "sheet_count": len(sheet_artifacts),
                "sheets": sheet_artifacts,
                "shape": {
                    "rows": int(sum(sheet["shape"]["rows"] for sheet in sheet_artifacts)),
                    "columns": int(sum(sheet["shape"]["columns"] for sheet in sheet_artifacts)),
                },
                "columns": [
                    {
                        **column,
                        "name": f"{sheet['sheet_name']}:{column['name']}",
                        "sheet_name": sheet["sheet_name"],
                    }
                    for sheet in sheet_artifacts
                    for column in sheet.get("columns", [])
                ],
                "sample": {
                    sheet["sheet_name"]: sheet.get("sample", [])
                    for sheet in sheet_artifacts
                },
            })
        elif ext == '.pdf':
            return extract_pdf_metadata(file_path, display_name)
        else:
            return {"error": f"Formato de arquivo '{ext}' não suportado."}
        
    except Exception as e:
        return {"error": f"Erro ao analisar o arquivo: {str(e)}"}


def extract_pdf_metadata(file_path: str, display_name: str) -> dict:
    """
    Extract lightweight PDF metadata without external parser dependencies.
    This is enough for prompt context while keeping generated scripts from treating PDFs as tables.
    """
    decoded = []
    page_count = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        page_count = len(reader.pages)
        for page in reader.pages[:5]:
            text = " ".join((page.extract_text() or "").split())
            if text:
                decoded.append(text[:1000])
    except Exception:
        with open(file_path, "rb") as f:
            content = f.read()

        page_count = len(re.findall(rb"/Type\s*/Page\b", content)) or None
        text_candidates = re.findall(rb"\(([^()]{4,200})\)", content)
        for chunk in text_candidates[:30]:
            try:
                text = chunk.decode("latin-1")
            except UnicodeDecodeError:
                continue
            text = re.sub(r"\\[nrtbf()\\]", " ", text)
            text = " ".join(text.split())
            if text:
                decoded.append(text)

    return {
        "filename": display_name,
        "type": "pdf",
        "size_bytes": os.path.getsize(file_path),
        "pages": page_count,
        "text_preview": decoded[:10],
        "note": "PDF metadata is available for context. It is not loaded as a pandas dataframe.",
    }
