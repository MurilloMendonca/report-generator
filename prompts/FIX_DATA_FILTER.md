# Skill: Data Preprocessing Repair
A pandas preprocessing function failed when it ran. Return a corrected version that performs the same transformation.

## Inputs
1. User goal.
2. Short description of the intended preprocessing.
3. Reported error (the end of the Python traceback).
4. Current failing function.
5. Available dataframe keys: the only valid keys of the `dataframes` dictionary.
6. Columns by dataframe key: the exact columns of each table.
7. Data artifact metadata for the raw uploaded files.

## Repair Process
1. Read the last lines of the traceback to find the failing line and exception.
2. Check every dictionary key against Available Dataframe Keys. Never use `Sheet1`, a sheet name alone, or a filename without its extension unless that exact string is listed.
3. Check every column against Columns by Dataframe Key for the table it is read from. Columns are not shared between tables: a column listed for one table does not exist in another. If a step uses a column of another table, merge the two tables first on a column that both lists contain, or delete the step.
4. If you cannot make a step work with the listed columns, delete it. A function that runs with fewer steps is better than one that fails.
5. Prefer simple pandas: boolean filters, `assign`, `groupby(...).agg(...)` followed by `reset_index()`, and `merge` on existing key columns. When adding a column computed from another table, merge first instead of assigning a Series with a different index.
6. Use current pandas aliases (for example `ME` for month end, `QE` for quarter end).

## Code Contract
- Define exactly one function with the same name as the failing code: `preprocess_dataframes(dataframes)` (or `filter_dataframes(dataframes)` if that is the name used).
- Return a dictionary with the same keys as the input. Copy every table that is not transformed.
- Never mutate the input DataFrames in place; use `.copy()`.
- Do not import libraries. Assume `pd` and `np` are already available.
- Do not print anything and do not read or write files.
- Keep the transformation the description asks for. Do not add unrelated steps.
- Never rename or drop existing columns; add new columns instead.

## Output Format
Return ONLY the raw Python code of the function. No explanations. No markdown fences.
