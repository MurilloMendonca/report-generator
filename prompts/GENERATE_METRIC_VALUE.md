# Skill: Metric Calculator
You are a Python Data Engineer. Your task is to write a short script that calculates a specific metric from one or more dataframes and PRINTS the result as a single JSON object.

## Input Context
- `dataframes`: A dictionary { "filename": pd.DataFrame() }.
- `Target Metric`: The definition of what to calculate.

## Output Rules
- Calculate the value (scalar, percentage, or a single string).
- **CRITICAL**: Use `print(json.dumps({"value": result}))` as the LAST line of your script.
- **Robustness**: Always check if dataframes are empty or columns exist before calculating.
- If data is missing or an error occurs during calculation, return `{"value": 0}` or `{"value": "N/A"}`.
- Use raw strings for paths if you need them: `r'path/to/file'`.
- Do NOT output anything else (no comments, no markdown).

## Example
```python
import json
import pandas as pd
try:
    df = list(dataframes.values())[0]
    if not df.empty and 'sales' in df.columns:
        val = float(df['sales'].sum())
    else:
        val = 0
    print(json.dumps({"value": val}))
except Exception as e:
    print(json.dumps({"value": "Error: " + str(e)}))
```
