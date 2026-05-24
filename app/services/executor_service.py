import subprocess
import os
import sys

def execute_script(script_content: str, script_path: str) -> dict:
    """
    Writes the script content to a file and executes it.
    Returns a dict with success status and logs.
    """
    try:
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        # Using the current python interpreter to run the generated script
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=30  # Safety timeout
        )
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e)
        }
