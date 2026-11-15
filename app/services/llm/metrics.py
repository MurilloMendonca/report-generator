"""Metric values computed by running model-written snippets in the sandbox."""

import json
import logging
import os
import time

from app.services.executor_service import SandboxUnavailableError
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.services.llm import client
from app.services.llm.client import llm_operation_context
from app.services.llm.errors import MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMTimeoutError
from app.services.llm.parsing import extract_json_objects, normalize_metric_value, strip_code_fence
from app.services.llm.prompts import load_prompt
from app.utils.report_paths import ReportPaths
from app.utils.tabular import TABULAR_EXTENSIONS
logger = logging.getLogger(__name__)


def dataframe_load_line(original_name: str, path: str, indent: str = "") -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in TABULAR_EXTENSIONS:
        return (
            f"{indent}from pdf_report_data import load_tabular_dataframes\n"
            f"{indent}dataframes.update(load_tabular_dataframes({original_name!r}, {path!r}))"
        )
    return ""


METRIC_ATTEMPTS = 3


def calculate_metric_value(
    all_metadata: str,
    artifact: dict,
    files_map: dict,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    job_id: str | None = None,
    paths: ReportPaths | None = None,
) -> any:
    """
    Generates and executes a script to calculate a metric value with automatic retries.
    """
    with llm_operation_context(job_id=job_id, operation="metric_value", provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_METRIC_VALUE.md")
        user_prompt = f"Target Metric:\n{json.dumps(artifact, indent=2)}\n\nData Artifacts:\n{all_metadata}"

        # Injetar carregamento dos dataframes para execução (com paths absolutos e raw strings)
        setup_code = ["import pandas as pd", "import json", "import os", "dataframes = {}"]
        for original_name, path in files_map.items():
            abs_path = os.path.abspath(path)
            line = dataframe_load_line(original_name, abs_path)
            if line:
                setup_code.append(line)

        execution_feedback = ""
        for attempt in range(METRIC_ATTEMPTS):
            try:
                code = client.call_llm(provider, model_name, system_instruction, f"{user_prompt}\n\nAttempt: {attempt+1}{execution_feedback}").strip()
                code = strip_code_fence(code, "python")

                full_script = "\n".join(setup_code) + "\n\n" + code

                from app.services.executor_service import execute_script
                import tempfile

                with tempfile.TemporaryDirectory(prefix="pdf_metric_") as metric_dir:
                    result = execute_script(full_script, os.path.join(metric_dir, "metric.py"), paths=paths)

                    if result["success"]:
                        output = result["stdout"].strip()
                        payload = next((item for item in reversed(extract_json_objects(output)) if "value" in item), None)
                        if payload is not None:
                            val = normalize_metric_value(payload.get("value"))
                            # An N/A often hides a wrong column or key; ask again before accepting it.
                            if val != "N/A" or attempt == METRIC_ATTEMPTS - 1:
                                return val
                            execution_feedback = (
                                "\nThe previous script printed N/A. Check every dataframe key and column against the "
                                "Data Artifacts and calculate the value from the full data. Keep N/A only if the data "
                                "really has no values for this metric."
                            )
                            continue

                    execution_feedback = f"\nPrevious execution failed: {(result.get('stderr') or result.get('stdout') or 'No JSON value was printed')[-1500:]}\nCorrect the script using this error."
                    logger.warning(
                        "metric execution failed metric_id=%s attempt=%s stderr=%s",
                        artifact.get("id", ""),
                        attempt + 1,
                        (result.get("stderr") or "")[:500],
                    )
            except (MissingCredentialError, ModelRefusalError, LLMBudgetError, LLMTimeoutError, SandboxUnavailableError):
                raise
            except Exception as e:
                logger.exception("metric execution exception metric_id=%s attempt=%s", artifact.get("id", ""), attempt + 1)

            time.sleep(1)

        return "N/A"
