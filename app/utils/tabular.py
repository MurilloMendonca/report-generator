"""Shared data loading for metadata profiling and generated sandbox scripts."""

import csv
import codecs
import os
import re

import pandas as pd


CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
CSV_SEPARATORS = (",", ";", "\t", "|")


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
