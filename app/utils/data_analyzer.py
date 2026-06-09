import os
import re
from collections import Counter

import numpy as np
import pandas as pd


CSV_ENCODINGS = ["utf-8", "utf-8-sig", "latin-1"]
CSV_SEPARATORS = [",", ";", "\t", "|"]
MAX_PROFILE_ROWS = 10000
MAX_PDF_TEXT_PAGES = 20
MAX_PDF_TEXT_CHARS = 20000
PDF_BINARY_MARKERS = (
    "/filter",
    "/flatedecode",
    "/length",
    "endstream",
    "endobj",
    " obj",
    "xref",
    "stream x",
)
PDF_FALLBACK_NOISE_MARKERS = (
    "x11;",
    "linux x86_64",
    "khtml",
    "like gecko",
    "mozilla/",
)
SENSITIVE_COLUMN_TOKENS = {
    "email", "e-mail", "mail", "telefone", "phone", "mobile", "celular",
    "cpf", "cnpj", "ssn", "document", "documento", "passport", "rg",
    "token", "secret", "senha", "password", "api_key", "apikey", "key",
    "address", "endereco", "endereço", "street", "rua",
}

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
    if name in {"year", "ano", "año"} and pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
        if not numeric.empty and numeric.between(1000, 3000).all():
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


def is_sensitive_column_name(name: str) -> bool:
    normalized = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return any(token in normalized for token in SENSITIVE_COLUMN_TOKENS)


def redacted_sample(df: pd.DataFrame, limit: int = 8) -> list:
    sample = df.head(limit).copy()
    for column in sample.columns:
        if is_sensitive_column_name(column):
            sample[column] = sample[column].map(lambda value: None if pd.isna(value) else "[REDACTED]")
    return sample.to_dict(orient="records")


def dataframe_metadata(df: pd.DataFrame, display_name: str, sheet_name: str = None, parser: dict = None) -> dict:
    rows, cols = df.shape
    columns_info = []
    for col in df.columns:
        series = df[col]
        dtype = str(series.dtype)
        null_count = int(series.isnull().sum())
        unique_count = int(series.nunique(dropna=True))

        sensitive = is_sensitive_column_name(col)
        col_info = {
            "name": str(col),
            "type": dtype,
            "role": infer_column_role(series),
            "null_count": null_count,
            "null_ratio": round(null_count / rows, 4) if rows else 0,
            "unique_values": unique_count,
            "unique_ratio": round(unique_count / rows, 4) if rows else 0,
            "top_values": [] if sensitive else top_values(series),
        }
        if sensitive:
            col_info["sensitive"] = True

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
        "sample": redacted_sample(df),
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


def clean_pdf_text_candidate(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"\\[nrtbf()\\]", " ", text)
    text = re.sub(r"\\[0-7]{1,3}", " ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", " ", text)
    return " ".join(text.split())


def pdf_text_quality(text: str) -> dict:
    if not text:
        return {
            "printable_ratio": 0,
            "control_ratio": 1,
            "replacement_ratio": 0,
            "alpha_numeric_ratio": 0,
            "high_bit_ratio": 0,
            "space_ratio": 0,
            "word_count": 0,
            "binary_marker_hits": 0,
        }

    total = len(text)
    printable = sum(1 for char in text if char.isprintable() or char.isspace())
    controls = sum(1 for char in text if not char.isprintable() and not char.isspace())
    replacements = text.count("\ufffd")
    alnum = sum(1 for char in text if char.isalnum())
    high_bit = sum(1 for char in text if ord(char) > 126)
    spaces = sum(1 for char in text if char.isspace())
    symbols = sum(1 for char in text if not char.isalnum() and not char.isspace() and char not in ".,;:!?%$€£¥+-/()")
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{2,}", text)
    lowered = text.lower()
    marker_hits = sum(1 for marker in PDF_BINARY_MARKERS if marker in lowered)
    noise_hits = sum(1 for marker in PDF_FALLBACK_NOISE_MARKERS if marker in lowered)

    return {
        "printable_ratio": round(printable / total, 4),
        "control_ratio": round(controls / total, 4),
        "replacement_ratio": round(replacements / total, 4),
        "alpha_numeric_ratio": round(alnum / total, 4),
        "high_bit_ratio": round(high_bit / total, 4),
        "space_ratio": round(spaces / total, 4),
        "symbol_ratio": round(symbols / total, 4),
        "word_count": len(words),
        "binary_marker_hits": marker_hits,
        "fallback_noise_hits": noise_hits,
    }


def is_usable_pdf_text(text: str, *, strict: bool = False) -> bool:
    quality = pdf_text_quality(text)
    if quality["binary_marker_hits"]:
        return False
    if quality["printable_ratio"] < 0.96:
        return False
    if quality["control_ratio"] > 0.01 or quality["replacement_ratio"] > 0:
        return False
    if quality["word_count"] < 3:
        return False
    if quality["alpha_numeric_ratio"] < 0.35:
        return False
    if strict and quality["space_ratio"] < 0.08:
        return False
    if quality["high_bit_ratio"] > (0.25 if strict else 0.4):
        return False
    if strict:
        if quality["word_count"] < 5:
            return False
        if quality["space_ratio"] < 0.12:
            return False
        if quality["alpha_numeric_ratio"] < 0.55:
            return False
        if quality["high_bit_ratio"] > 0.12:
            return False
        if quality["symbol_ratio"] > 0.08:
            return False
        if quality["fallback_noise_hits"]:
            return False
    return True


def extract_pdf_metadata(file_path: str, display_name: str) -> dict:
    """
    Extract lightweight PDF metadata without external parser dependencies.
    This is enough for prompt context while keeping generated scripts from treating PDFs as tables.
    """
    decoded = []
    page_count = None
    discarded_candidates = 0
    extraction_method = "pypdf"
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        page_count = len(reader.pages)
        for page in reader.pages[:MAX_PDF_TEXT_PAGES]:
            text = clean_pdf_text_candidate(page.extract_text() or "")
            if text and is_usable_pdf_text(text):
                decoded.append(text)
            elif text:
                discarded_candidates += 1
    except Exception:
        extraction_method = "raw_pdf_fallback"
        with open(file_path, "rb") as f:
            content = f.read()

        page_count = len(re.findall(rb"/Type\s*/Page\b", content)) or None
        text_candidates = re.findall(rb"\(([^()]{4,200})\)", content)
        for chunk in text_candidates[:30]:
            try:
                text = chunk.decode("latin-1")
            except UnicodeDecodeError:
                discarded_candidates += 1
                continue
            text = clean_pdf_text_candidate(text)
            if text and is_usable_pdf_text(text, strict=True):
                decoded.append(text)
            elif text:
                discarded_candidates += 1

    text_excerpt = " ".join(decoded)
    full_text_length = len(text_excerpt)
    if len(text_excerpt) > MAX_PDF_TEXT_CHARS:
        text_excerpt = text_excerpt[:MAX_PDF_TEXT_CHARS].rsplit(" ", 1)[0]
    quality = "usable" if text_excerpt else "unusable"
    warnings = []
    if discarded_candidates and not text_excerpt:
        warnings.append("Extracted PDF text looked binary or encoded and was discarded.")
    elif not text_excerpt:
        warnings.append("No usable text could be extracted from this PDF.")

    return {
        "filename": display_name,
        "type": "pdf",
        "size_bytes": os.path.getsize(file_path),
        "pages": page_count,
        "text_preview": [text[:1000] for text in decoded[:10]],
        "text_excerpt": text_excerpt,
        "text_extraction": {
            "pages_attempted": MAX_PDF_TEXT_PAGES,
            "characters_extracted": len(text_excerpt),
            "truncated": full_text_length > len(text_excerpt),
            "method": extraction_method,
            "quality": quality,
            "discarded_candidates": discarded_candidates,
            "warnings": warnings,
        },
        "note": "PDF metadata is available for context. It is not loaded as a pandas dataframe.",
    }
