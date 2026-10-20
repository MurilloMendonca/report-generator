"""Shared data loading for metadata profiling and generated sandbox scripts."""

import csv
import codecs
import json
import math
import os
import re

import pandas as pd


CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
CSV_SEPARATORS = (",", ";", "\t", "|")
JSON_EXTENSIONS = {".json", ".jsonl", ".ndjson"}
TABULAR_EXTENSIONS = {".csv", ".xls", ".xlsx"} | JSON_EXTENSIONS


def reject_json_constant(value):
    raise ValueError("JSON must not contain NaN or Infinity")


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains duplicate object keys")
        result[key] = value
    return result


def parse_json(text: str):
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON numbers must be finite")
        return number
    payload = json.loads(text, parse_constant=reject_json_constant, parse_float=finite_float,
                         object_pairs_hook=unique_json_object)

    def validate_depth(value, depth=0):
        if depth > 32:
            raise ValueError("JSON nesting exceeds 32 levels")
        if isinstance(value, dict):
            for child in value.values():
                validate_depth(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                validate_depth(child, depth + 1)

    validate_depth(payload)
    return payload


def json_records(payload) -> tuple[list, str]:
    """Find an unambiguous record array, or treat a plain object as one row."""
    if isinstance(payload, list):
        if not payload or not all(isinstance(row, dict) and row for row in payload):
            raise ValueError("JSON data must be a non-empty array of objects")
        return payload, ""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("JSON data must be an object or an array of objects")
    candidates = []

    def find_arrays(value, pointer="", depth=0):
        if depth > 32:
            raise ValueError("JSON nesting exceeds 32 levels")
        for key, child in value.items():
            child_pointer = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if isinstance(child, list) and (not child or all(isinstance(row, dict) for row in child)):
                candidates.append((child, child_pointer))
            elif isinstance(child, dict):
                find_arrays(child, child_pointer, depth + 1)

    find_arrays(payload)
    if len(candidates) > 1:
        raise ValueError("JSON contains multiple record arrays; select one with the API records path or upload that array separately")
    if candidates:
        records, pointer = candidates[0]
        return json_records(records)[0], pointer
    return [payload], ""


def read_json_with_detection(path: str) -> tuple[pd.DataFrame, dict]:
    lines = os.path.splitext(path)[1].lower() in {".jsonl", ".ndjson"}
    try:
        with open(path, encoding="utf-8-sig") as source:
            payload = [parse_json(line) for line in source if line.strip()] if lines else parse_json(source.read())
        records, pointer = json_records(payload)
        # Keep lists as JSON text: no implicit row multiplication or unhashable cells.
        flat_records = []
        for record in records:
            flat = {}

            def flatten(value, prefix="", depth=0):
                if depth > 32:
                    raise ValueError("JSON nesting exceeds 32 levels")
                for key, child in value.items():
                    name = f"{prefix}.{key}" if prefix else key
                    if isinstance(child, dict) and child:
                        flatten(child, name, depth + 1)
                    else:
                        if name in flat:
                            raise ValueError("JSON contains conflicting flattened column names")
                        flat[name] = json.dumps(child, ensure_ascii=False) if isinstance(child, (list, dict)) else child

            flatten(record)
            flat_records.append(flat)
        dataframe = pd.DataFrame.from_records(flat_records)
        if not len(dataframe.columns):
            raise ValueError("JSON data has no columns")
        return dataframe, {"format": "jsonl" if lines else "json", "records_path": pointer, "encoding": "utf-8-sig"}
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("JSON must use UTF-8 and have bounded nesting") from exc


def detect_csv_parser(path: str) -> dict:
    with open(path, "rb") as source:
        sample = source.read(65536)
    text = None
    for encoding in CSV_ENCODINGS:
        try:
            text = codecs.getincrementaldecoder(encoding)().decode(sample, final=False)
            break
        except UnicodeDecodeError:
            continue
    if not text or not text.strip():
        raise ValueError("CSV file is empty")
    try:
        separator = csv.Sniffer().sniff(text, delimiters="".join(CSV_SEPARATORS)).delimiter
    except csv.Error:
        candidates = []
        for delimiter in CSV_SEPARATORS:
            try:
                preview = pd.read_csv(path, encoding=encoding, sep=delimiter, nrows=200)
                candidates.append((len(preview.columns), delimiter))
            except (ValueError, pd.errors.ParserError, UnicodeError):
                continue
        if not candidates:
            raise ValueError("Could not detect the CSV format")
        separator = max(candidates, key=lambda item: item[0])[1]
    parser = {"encoding": encoding, "separator": separator, "decimal": "."}
    if separator != ",":
        rows = list(csv.reader(text.splitlines()[:100], delimiter=separator))
        values = [value.strip() for row in rows[1:] for value in row]
        comma_decimals = sum(bool(re.fullmatch(r"[-+]?\d+,\d+", value)) for value in values)
        dot_decimals = sum(bool(re.fullmatch(r"[-+]?\d+\.\d+", value)) for value in values)
        if comma_decimals > dot_decimals:
            parser["decimal"] = ","
    return parser


def read_csv_with_detection(path: str, nrows: int | None = None) -> tuple[pd.DataFrame, dict]:
    parser = detect_csv_parser(path)
    dataframe = pd.read_csv(
        path, encoding=parser["encoding"], sep=parser["separator"],
        decimal=parser["decimal"], nrows=nrows,
    )
    return dataframe, parser


def load_tabular_dataframes(name: str, path: str) -> dict[str, pd.DataFrame]:
    extension = os.path.splitext(path)[1].lower()
    if extension == ".csv":
        return {name: read_csv_with_detection(path)[0]}
    if extension in JSON_EXTENSIONS:
        return {name: read_json_with_detection(path)[0]}
    if extension in {".xls", ".xlsx"}:
        sheets = pd.read_excel(path, sheet_name=None)
        return {
            name if index == 0 else f"{name}::{sheet}": dataframe.rename(columns=str)
            for index, (sheet, dataframe) in enumerate(sheets.items())
        }
    return {}


def metadata_tables(metadata_items: list):
    for item in metadata_items or []:
        yield item
        for index, sheet in enumerate(item.get("sheets") or []):
            yield {
                **sheet,
                "filename": sheet.get("dataframe_key") or (
                    item["filename"] if index == 0 else f"{item['filename']}::{sheet['sheet_name']}"
                ),
            }
