"""Detect key relationships between tabular datasets from their actual values.

A relationship is reported in the many-to-one direction: every row of
``from`` may point to at most one row of ``to``. Joining in that direction
keeps the row count of ``from``, so totals computed on it are not multiplied.
"""
import re
import unicodedata

import pandas as pd

MIN_COVERAGE = 0.6
MAX_DISTINCT = 200_000
MAX_RELATIONSHIPS = 50
_ID_PATTERNS = (re.compile(r"^(?:id|cod|codigo|code|key|chave)_(.+)$"), re.compile(r"^(.+)_(?:id|cod|codigo|code|key|chave)$"))


def _normalize_name(name) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.strip().casefold()).strip("_")


def _singular(word: str) -> str:
    for suffix, replacement in (("ies", "y"), ("oes", "ao"), ("aes", "ao"), ("s", "")):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + replacement
    return word


def _dataset_stem(key: str) -> str:
    """'data/Customers.csv::Sheet' -> 'customer' (sheet name wins for workbooks)."""
    text = str(key).split("::")[-1]
    text = re.sub(r"\.[A-Za-z0-9]+$", "", text.rsplit("/", 1)[-1])
    return _singular(_normalize_name(text))


def _key_stem(column: str) -> str | None:
    """'customer_id' / 'id_customer' -> 'customer'."""
    for pattern in _ID_PATTERNS:
        match = pattern.match(column)
        if match:
            return _singular(match.group(1))
    return None


def key_values(series: pd.Series) -> pd.Series:
    """Comparable text keys: 7, 7.0 and '7' match; '07' stays distinct."""
    values = series.dropna()
    if pd.api.types.is_bool_dtype(values):
        return values.astype(str)
    if pd.api.types.is_numeric_dtype(values):
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        if not (numeric == numeric.round()).all():
            return pd.Series(dtype=object)
        return numeric.astype("int64").astype(str)
    if pd.api.types.is_datetime64_any_dtype(values):
        return pd.Series(dtype=object)
    return values.astype(str).str.strip().replace("", pd.NA).dropna()


_KEY_WORDS = {"id", "cod", "codigo", "code", "key", "chave", "sku", "ano", "year", "mes", "month",
              "numero", "number", "num", "nr", "cpf", "cnpj", "uf", "sigla", "matricula", "registro"}


def _key_like(series: pd.Series, name: str) -> bool:
    """Text columns may be keys; numbers only when the name says so (not amounts)."""
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return not pd.api.types.is_datetime64_any_dtype(series)
    return bool(set(name.split("_")) & _KEY_WORDS)


def _candidate_pairs(left_key, left_df, right_key, right_df):
    left_stem, right_stem = _dataset_stem(left_key), _dataset_stem(right_key)
    right_names = {_normalize_name(column): column for column in right_df.columns}
    for left_column in left_df.columns:
        name = _normalize_name(left_column)
        if not name:
            continue
        if name in right_names and _key_like(left_df[left_column], name) and _key_like(right_df[right_names[name]], name):
            yield left_column, right_names[name], "same_name"
        stem = _key_stem(name)
        if stem and stem == right_stem:
            # orders.customer_id -> customers.id / customers.customer
            for target in ("id", "codigo", "code", "key", right_stem):
                if target in right_names and target != name:
                    yield left_column, right_names[target], "key_name"
        if name in {"id", "codigo", "code", "key"} and left_stem:
            for right_name, right_column in right_names.items():
                if _key_stem(right_name) == left_stem:
                    yield left_column, right_column, "key_name"


def _profile(dataframe, column, cache):
    marker = (id(dataframe), column)
    if marker not in cache:
        values = key_values(dataframe[column])
        distinct = set(values.unique()) if len(values) and values.nunique() <= MAX_DISTINCT else set()
        cache[marker] = {"values": distinct, "non_null": int(len(values)), "unique": bool(len(values)) and values.is_unique}
    return cache[marker]


def detect_relationships(dataframes: dict) -> list[dict]:
    """Pairs of columns whose values link two datasets, verified on the data."""
    tables = {key: df for key, df in (dataframes or {}).items() if isinstance(df, pd.DataFrame) and not df.empty}
    found, seen, cache = [], set(), {}
    keys = list(tables)
    for left_key in keys:
        for right_key in keys:
            if left_key == right_key:
                continue
            left_df, right_df = tables[left_key], tables[right_key]
            for left_column, right_column, reason in _candidate_pairs(left_key, left_df, right_key, right_df):
                marker = (left_key, str(left_column), right_key, str(right_column))
                if marker in seen:
                    continue
                seen.add(marker)
                left, right = _profile(left_df, left_column, cache), _profile(right_df, right_column, cache)
                if not left["values"] or not right["values"] or not right["unique"]:
                    continue  # report only directions where each row finds at most one match
                matched = len(left["values"] & right["values"])
                coverage = matched / len(left["values"])
                if coverage < MIN_COVERAGE:
                    continue
                found.append({
                    "from": {"dataset": left_key, "column": str(left_column)},
                    "to": {"dataset": right_key, "column": str(right_column)},
                    "cardinality": "one_to_one" if left["unique"] else "many_to_one",
                    "coverage": round(coverage, 4),
                    "matched_values": matched,
                    "from_distinct": len(left["values"]),
                    "to_rows": right["non_null"],
                    "match_reason": reason,
                    "joinable": True,
                })
    found.sort(key=lambda item: (-item["coverage"], item["from"]["dataset"], item["from"]["column"]))
    return found[:MAX_RELATIONSHIPS]
