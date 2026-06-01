# Skill: Data Preprocessing Planner
You generate an optional pandas preprocessing function for report data.

## Objective
Decide whether the user's goal asks for data preprocessing before analysis. If it does, return Python code that transforms the loaded pandas DataFrames once, before any metrics or components are generated.

Preprocessing can include filtering rows, adding calculated columns, normalizing values, renaming or deriving useful fields, grouping/aggregating records, or otherwise preparing the data so the rest of the report uses the intended analytical dataset.

## Inputs
1. Target language.
2. User goal / refinement request.
3. Data artifact metadata for the raw uploaded files.

## Output Format
Return ONLY a JSON object:

```json
{
  "needs_preprocessing": true,
  "description": "Short user-facing description of the preprocessing.",
  "code": "def preprocess_dataframes(dataframes):\n    processed = {}\n    ...\n    return processed"
}
```

If no preprocessing is needed:

```json
{
  "needs_preprocessing": false,
  "description": "",
  "code": ""
}
```

## Code Contract
- Define exactly one function: `preprocess_dataframes(dataframes)`.
- `dataframes` is a dictionary mapping original filenames to pandas DataFrames.
- Return a dictionary with filenames as keys and processed DataFrames as values.
- Never mutate the input DataFrames in place; use `.copy()`.
- Do not import libraries. Assume `pd` and `np` are already available.
- Preserve all files unless the user's request explicitly asks to drop or replace a dataset.
- If a file should not be changed, return a copy unchanged.
- Make transformations robust to missing columns by leaving that DataFrame unchanged.
- Use case-insensitive string matching where appropriate.
- Do not hardcode row indexes.
- Do not print anything.
- Aggregations are allowed when requested, but keep output columns clearly named.
- Calculated columns are allowed when requested or clearly useful to satisfy the user's stated analysis goal.

## Preprocessing Detection
Generate preprocessing when the user explicitly asks to filter, include, exclude, ignore, remove, keep, restrict, focus on, "do not include", "não inclua", "nenhuma", add/create/calculate a column, group/aggregate/summarize records, normalize/standardize/clean values, bucket/bin values, or otherwise transform the input data before analysis.

Otherwise return `needs_preprocessing: false`.
