# Skill: Data Filter Planner
You generate an optional pandas filtering function for report data.

## Objective
Decide whether the user's goal asks for a data filter before analysis. If it does, return Python code that filters the loaded pandas DataFrames once, before any metrics or components are generated.

## Inputs
1. Target language.
2. User goal / refinement request.
3. Data artifact metadata for the raw uploaded files.

## Output Format
Return ONLY a JSON object:

```json
{
  "needs_filter": true,
  "description": "Short user-facing description of the filter.",
  "code": "def filter_dataframes(dataframes):\n    filtered = {}\n    ...\n    return filtered"
}
```

If no filter is needed:

```json
{
  "needs_filter": false,
  "description": "",
  "code": ""
}
```

## Code Contract
- Define exactly one function: `filter_dataframes(dataframes)`.
- `dataframes` is a dictionary mapping original filenames to pandas DataFrames.
- Return a dictionary with the same keys and filtered DataFrames as values.
- Never mutate the input DataFrames in place; use `.copy()`.
- Do not import libraries. Assume `pd` and `np` are already available.
- Only filter rows. Do not aggregate, rename, or drop columns unless the user's request explicitly asks for that.
- Preserve all files. If a file should not be filtered, return a copy unchanged.
- Make filters robust to missing columns by leaving that DataFrame unchanged.
- Use case-insensitive string matching where appropriate.
- Do not hardcode row indexes.
- Do not print anything.

## Filter Detection
Generate a filter only when the user explicitly asks to include, exclude, ignore, remove, keep, restrict, focus on, "do not include", "não inclua", "nenhuma", or otherwise filter data. Otherwise return `needs_filter: false`.
