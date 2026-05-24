import pandas as pd
import os
import numpy as np

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

def extract_metadata(file_path: str, original_filename: str = None) -> dict:
    """
    Reads a CSV or Excel file and returns a rich dictionary summarizing its structure and content.
    """
    ext = os.path.splitext(file_path)[1].lower()
    display_name = original_filename or os.path.basename(file_path)
    
    try:
        if ext == '.csv':
            df = pd.read_csv(file_path)
        elif ext in ['.xls', '.xlsx']:
            df = pd.read_excel(file_path)
        else:
            return {"error": f"Formato de arquivo '{ext}' não suportado."}
        
        # Basic shape
        rows, cols = df.shape
        
        # Detailed column info
        columns_info = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            null_count = int(df[col].isnull().sum())
            unique_count = int(df[col].nunique())
            
            col_info = {
                "name": col,
                "type": dtype,
                "null_count": null_count,
                "unique_values": unique_count
            }
            
            # Add basic stats for numeric columns
            if "int" in dtype or "float" in dtype:
                mean_val = df[col].mean()
                min_val = df[col].min()
                max_val = df[col].max()
                
                col_info["stats"] = {
                    "mean": float(mean_val) if pd.notnull(mean_val) else None,
                    "min": float(min_val) if pd.notnull(min_val) else None,
                    "max": float(max_val) if pd.notnull(max_val) else None
                }
            
            columns_info.append(col_info)
        
        # Sample data (top 5 rows) as a list of dicts
        sample_data = df.head(5).to_dict(orient='records')
        
        artifact = {
            "filename": display_name,
            "shape": {"rows": rows, "columns": cols},
            "columns": columns_info,
            "sample": sample_data
        }
        
        # Robust sanitization for JSON compliance
        return sanitize_data(artifact)
        
    except Exception as e:
        return {"error": f"Erro ao analisar o arquivo: {str(e)}"}
