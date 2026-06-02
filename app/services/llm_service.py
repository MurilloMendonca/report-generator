import os
import hashlib
import json
import re
import time
from dotenv import load_dotenv
from openai import OpenAI
from app.utils.layout import normalize_template
from app.utils.template_library import build_layout_from_template

load_dotenv()

PROMPTS_DIR = "prompts"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL_BY_PROVIDER = {
    "deepseek": "deepseek-v4-flash",
    "gemini": "gemini-3.1-flash-lite-preview",
}
DEFAULT_MODEL_NAME = DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER]


MODEL_CATALOG = {
    "gemini": [
        {"id": "gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash Lite Preview"},
        {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash Lite"},
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview"},
        {"id": "gemini-3-pro-preview", "label": "Gemini 3 Pro Preview"},
        {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview"},
        {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash"},
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite"},
        {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
        {"id": "gemini-2.0-flash-001", "label": "Gemini 2.0 Flash 001"},
        {"id": "gemini-2.0-flash-lite", "label": "Gemini 2.0 Flash Lite"},
        {"id": "gemini-2.0-flash-lite-001", "label": "Gemini 2.0 Flash Lite 001"},
        {"id": "gemini-pro-latest", "label": "Gemini Pro Latest"},
        {"id": "gemini-flash-latest", "label": "Gemini Flash Latest"},
        {"id": "gemini-flash-lite-latest", "label": "Gemini Flash Lite Latest"},
        {"id": "gemma-4-31b-it", "label": "Gemma 4 31B IT"},
        {"id": "gemma-4-26b-a4b-it", "label": "Gemma 4 26B A4B IT"},
    ],
    "deepseek": [
        {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    ],
}


def model_catalog() -> dict:
    return {
        "default_provider": DEFAULT_PROVIDER,
        "default_model": DEFAULT_MODEL_NAME,
        "providers": MODEL_CATALOG,
    }


def strip_code_fence(text: str, language: str = None) -> str:
    text = text.strip()
    fence = f"```{language}" if language else "```"
    if language and fence in text:
        return text.split(fence, 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def extract_json_payload(text: str, opening: str, closing: str) -> str:
    text = strip_code_fence(text, "json")
    if opening in text and closing in text:
        return text[text.find(opening):text.rfind(closing) + 1]
    return text


def extract_json_objects(text: str) -> list:
    decoder = json.JSONDecoder()
    objects = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
            if isinstance(value, dict):
                objects.append(value)
            index = start + max(end, 1)
        except json.JSONDecodeError:
            index = start + 1
    return objects


def normalize_metric_value(value):
    if value is None:
        return "N/A"
    if isinstance(value, float) and value != value:
        return "N/A"
    if isinstance(value, dict):
        cleaned = {}
        for key, raw_value in value.items():
            normalized_value = normalize_metric_value(raw_value)
            if normalized_value != "N/A":
                cleaned[str(key)] = normalized_value
        return cleaned or "N/A"
    if isinstance(value, list):
        cleaned = [normalize_metric_value(v) for v in value]
        cleaned = [v for v in cleaned if v != "N/A"]
        return cleaned or "N/A"
    if isinstance(value, str):
        value = value.strip()
        return value if value and "Error" not in value else "N/A"
    return value

def load_prompt(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file {filename} not found.")
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def prompt_fingerprint(filename: str) -> dict:
    content = load_prompt(filename)
    return {
        "filename": filename,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "version": content.splitlines()[0].strip("# ").strip() if content.splitlines() else filename,
    }

def normalize_gemini_model_name(model_name: str) -> str:
    return str(model_name or "").removeprefix("models/")


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment.")
    try:
        from google import genai
    except ImportError as exc:
        raise ImportError("google-genai is required for Gemini. Install dependencies with `pip install -r requirements.txt`.") from exc
    return genai.Client(api_key=api_key)

def get_deepseek_client():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not found in environment.")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

def resolve_llm_config(provider: str = None, model_name: str = None) -> tuple[str, str]:
    provider = provider or DEFAULT_PROVIDER
    model_name = model_name or DEFAULT_MODEL_BY_PROVIDER.get(provider)
    if not model_name:
        raise ValueError(f"No default model configured for provider '{provider}'.")
    return provider, model_name


def call_llm(provider: str, model_name: str, system_instruction: str, user_prompt: str, retries=3, delay=10):
    provider, model_name = resolve_llm_config(provider, model_name)
    for i in range(retries):
        try:
            if provider == "gemini":
                client = get_gemini_client()
                response = client.models.generate_content(
                    model=normalize_gemini_model_name(model_name),
                    contents=f"{system_instruction}\n\n{user_prompt}",
                )
                return response.text
            elif provider == "deepseek":
                client = get_deepseek_client()
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": user_prompt},
                    ],
                    stream=False
                )
                return response.choices[0].message.content
        except Exception as e:
            if "429" in str(e) or (hasattr(e, 'status_code') and e.status_code == 429):
                if i < retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
            raise e

def generate_artifacts(all_metadata: str, prompt: str, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None, template_context: dict = None) -> list:
    system_instruction = load_prompt("GENERATE_ARTIFACTS.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    layout_constraints = ""
    if template_context:
        layout_constraints = (
            "Selected Layout Template:\n"
            f"{json.dumps(template_context, indent=2)}\n\n"
            "Hard constraint: generate visible text and graphic artifacts that fit this exact template. "
            "Metric artifacts can exceed the visual slot counts because they are not directly rendered.\n\n"
        )
    user_prompt = f"Target Language: {lang}\n{layout_constraints}Data Artifacts:\n{all_metadata}\n\nUser's Initial Prompt: {prompt}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = extract_json_payload(text, "[", "]")
            
        try:
            artifacts = json.loads(text)
            if isinstance(artifacts, list) and len(artifacts) > 0:
                return artifacts
        except:
            time.sleep(2)
            
    return []

def generate_template(artifacts: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, template_id: str = None) -> list:
    return normalize_template(build_layout_from_template(artifacts, template_id))


def normalize_preprocessing_payload(payload: dict) -> dict:
    needs_preprocessing = bool(payload.get("needs_preprocessing") or payload.get("needs_filter"))
    if not needs_preprocessing:
        return {
            "needs_preprocessing": False,
            "needs_filter": False,
            "description": "",
            "code": "",
        }

    code = strip_code_fence(str(payload.get("code", "")), "python").strip()
    if "def preprocess_dataframes" not in code and "def filter_dataframes" not in code:
        return {
            "needs_preprocessing": False,
            "needs_filter": False,
            "description": "",
            "code": "",
        }

    return {
        "needs_preprocessing": True,
        "needs_filter": True,
        "description": str(payload.get("description", "")).strip(),
        "code": code,
    }


def generate_data_filter(all_metadata: str, prompt: str, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None) -> dict:
    system_instruction = load_prompt("GENERATE_DATA_FILTER.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    user_prompt = (
        f"Target Language: {lang}\n"
        f"User Goal:\n{prompt}\n\n"
        f"Raw Data Artifacts:\n{all_metadata}"
    )

    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = extract_json_payload(text, "{", "}")
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return normalize_preprocessing_payload(payload)
        except Exception as e:
            print(f"DEBUG: data preprocessing generation attempt {attempt + 1} failed: {str(e)}")
            time.sleep(2)

    return {"needs_preprocessing": False, "needs_filter": False, "description": "", "code": ""}


def dataframe_load_line(original_name: str, path: str, indent: str = "") -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        return f"{indent}dataframes[{json.dumps(original_name)}] = pd.read_csv(r'{path}')"
    if ext in ['.xls', '.xlsx']:
        return f"{indent}dataframes[{json.dumps(original_name)}] = pd.read_excel(r'{path}')"
    return ""

def calculate_metric_value(all_metadata: str, artifact: dict, files_map: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME) -> any:
    """
    Generates and executes a script to calculate a metric value with automatic retries.
    """
    system_instruction = load_prompt("GENERATE_METRIC_VALUE.md")
    user_prompt = f"Target Metric:\n{json.dumps(artifact, indent=2)}\n\nData Artifacts:\n{all_metadata}"
    
    # Injetar carregamento dos dataframes para execução (com paths absolutos e raw strings)
    setup_code = ["import pandas as pd", "import json", "import os", "dataframes = {}"]
    for original_name, path in files_map.items():
        abs_path = os.path.abspath(path)
        line = dataframe_load_line(original_name, abs_path)
        if line:
            setup_code.append(line)
    
    for attempt in range(3):
        try:
            code = call_llm(provider, model_name, system_instruction, f"{user_prompt}\n\nAttempt: {attempt+1}").strip()
            code = strip_code_fence(code, "python")

            full_script = "\n".join(setup_code) + "\n\n" + code
            
            from app.services.executor_service import execute_script
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
                result = execute_script(full_script, tmp.name)
                os.unlink(tmp.name) # Cleanup manual
                
                if result["success"]:
                    output = result["stdout"].strip()
                    for payload in reversed(extract_json_objects(output)):
                        val = normalize_metric_value(payload.get("value"))
                        if val != "N/A":
                            return val
                
                # Se falhou, logamos o erro para debug (interno)
                print(f"DEBUG: Metric {artifact['id']} attempt {attempt+1} failed: {result['stderr']}")
        except Exception as e:
            print(f"DEBUG: Metric {artifact['id']} exception: {str(e)}")
            
        time.sleep(1)
        
    return "N/A"

VISUAL_COMPONENT_TYPES = {'graphic', 'bar', 'line', 'pie', 'scatter', 'table', 'chart'}


def chart_code_has_layout_risk(code: str) -> bool:
    lowered = str(code or "").lower()
    has_x_label = bool(re.search(r"\.set_xlabel\(\s*['\"][^'\"]+['\"]", lowered))
    has_bottom_legend = bool(re.search(r"bbox_to_anchor\s*=\s*\([^)]*-\d*\.?\d+", lowered))
    if has_x_label and has_bottom_legend:
        return True

    has_data_annotation = any(token in lowered for token in [
        "ax.annotate",
        "plt.annotate",
        "ax.text(",
        "plt.text(",
    ])
    if not has_data_annotation:
        return False

    has_inside_upper_legend = any(token in lowered for token in [
        "legend(loc='upper right'",
        'legend(loc="upper right"',
        "legend(loc='upper left'",
        'legend(loc="upper left"',
        "legend(loc='best'",
        'legend(loc="best"',
    ])
    if not has_inside_upper_legend:
        return False

    return "bbox_to_anchor" not in lowered


def slot_by_artifact_id(template: list) -> dict:
    return {item.get("id"): item for item in normalize_template(template or []) if item.get("id")}


def visual_slot_guidance(block: dict | None) -> dict | None:
    if not block:
        return None
    width = block.get("w") or 0
    height = block.get("h") or 0
    compact = width < 110 or height < 75
    return {
        "page": block.get("page", 1),
        "x_mm": block.get("x"),
        "y_mm": block.get("y"),
        "w_mm": width,
        "h_mm": height,
        "compact": compact,
        "guidance": (
            "Compact chart block: prioritize a simple chart, small title, no non-essential data labels, "
            "and avoid bottom legend plus x-axis label."
            if compact else
            "Larger chart block: reserve explicit margins for title, tick labels, and legend."
        ),
    }


def text_layout_guidance(text_artifacts: list, template: list, settings: dict = None) -> list:
    blocks_by_id = slot_by_artifact_id(template)
    guidance = []
    for artifact in text_artifacts:
        artifact_id = artifact.get("id")
        block = blocks_by_id.get(artifact_id)
        if not block:
            continue
        capacity = estimate_text_block_capacity(block, settings)
        guidance.append({
            "id": artifact_id,
            "indicator": artifact.get("indicator", artifact_id),
            "slot": {
                "page": block.get("page", 1),
                "w_mm": block.get("w"),
                "h_mm": block.get("h"),
            },
            "target_words": capacity["target_words"],
            "soft_max_words": capacity["soft_max_words"],
            "max_lines": capacity["max_lines"],
        })
    return guidance


def generate_component_code(all_metadata: str, artifact: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, calculated_metrics: dict = None, preceding_text: str = None, settings: dict = None, layout_slot: dict = None) -> str:
    system_instruction = load_prompt("GENERATE_COMPONENT_CODE.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    
    context = [
        f"Target Language: {lang}",
        f"Target Component JSON:\n{json.dumps(artifact, indent=2)}",
        f"Data Artifacts:\n{all_metadata}"
    ]
    
    if calculated_metrics:
        context.append(f"Calculated Metrics (Available for reference):\n{json.dumps(calculated_metrics, indent=2)}")
    
    if preceding_text:
        context.append(f"Preceding Report Text (for context/conclusion):\n{preceding_text}")

    slot_guidance = visual_slot_guidance(layout_slot)
    if slot_guidance:
        context.append(f"Layout Slot Constraints:\n{json.dumps(slot_guidance, indent=2, ensure_ascii=False)}")
        
    user_prompt = "\n\n".join(context)

    last_code = ""
    for attempt in range(3):
        attempt_prompt = user_prompt
        if attempt > 0:
            attempt_prompt += (
                "\n\nPrevious attempt violated chart layout safety: it combined data labels "
                "or annotations with an inside upper-corner legend. Regenerate the function "
                "with the legend outside the axes using bbox_to_anchor, or remove non-essential "
                "data labels/annotations."
            )

        text = call_llm(provider, model_name, system_instruction, attempt_prompt)
        code = strip_code_fence(text, "python")
        last_code = code
        if artifact.get("type") not in VISUAL_COMPONENT_TYPES or not chart_code_has_layout_risk(code):
            return code
        time.sleep(1)

    return last_code

def generate_all_texts(all_metadata: str, text_artifacts: list, calculated_metrics: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None, template: list = None) -> dict:
    system_instruction = load_prompt("GENERATE_ALL_TEXTS.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    layout_guidance = text_layout_guidance(text_artifacts, template or [], settings)
    context = [
        f"Target Language: {lang}",
        f"Text Artifacts:\n{json.dumps(text_artifacts, indent=2, ensure_ascii=False)}",
        f"Calculated Metrics:\n{json.dumps(calculated_metrics, indent=2, ensure_ascii=False)}",
    ]
    if layout_guidance:
        context.append(f"Layout Slots:\n{json.dumps(layout_guidance, indent=2, ensure_ascii=False)}")
    context.append(f"Data Artifacts:\n{all_metadata}")
    user_prompt = "\n\n".join(context)
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = extract_json_payload(text, "{", "}")

        try:
            return json.loads(text)
        except:
            time.sleep(2)
    return {}


def estimate_text_block_capacity(block: dict, settings: dict = None) -> dict:
    settings = settings or {}
    font_size = settings.get("font_size", 10) or 10
    line_height = settings.get("line_height", 5) or 5
    title_reserve = 6 if (block.get("h") or 0) < 55 else 7
    body_height = max(1, (block.get("h") or 0) - title_reserve)
    chars_per_line = max(18, int((block.get("w") or 0) * 0.62 * (10 / font_size)))
    max_lines = max(1, int(body_height / max(line_height, 1)))
    raw_words = chars_per_line * max_lines / 6.2
    target_words = max(24, int(raw_words * 0.92))
    soft_max_words = max(target_words + 8, int(raw_words * 1.08))
    return {
        "chars_per_line": chars_per_line,
        "max_lines": max_lines,
        "max_chars": int(chars_per_line * max_lines * 1.02),
        "target_words": target_words,
        "soft_max_words": soft_max_words,
    }


def estimated_line_count(text: str, chars_per_line: int) -> int:
    total = 0
    for paragraph in str(text or "").splitlines() or [""]:
        total += max(1, len(paragraph.strip()) // max(chars_per_line, 1) + 1)
    return total


def condense_texts_to_fit(texts_map: dict, text_artifacts: list, template: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None) -> dict:
    texts_map = dict(texts_map or {})
    blocks_by_id = {item.get("id"): item for item in normalize_template(template)}
    overflow_items = []

    for artifact in text_artifacts:
        artifact_id = artifact.get("id")
        block = blocks_by_id.get(artifact_id)
        text = texts_map.get(artifact_id)
        if not block or not text:
            continue
        capacity = estimate_text_block_capacity(block, settings)
        lines = estimated_line_count(text, capacity["chars_per_line"])
        overflow_threshold = max(capacity["max_lines"] + 1, int(capacity["max_lines"] * 1.18))
        if lines > overflow_threshold:
            overflow_items.append({
                "id": artifact_id,
                "indicator": artifact.get("indicator", artifact_id),
                "slot": {"w_mm": block.get("w"), "h_mm": block.get("h")},
                "estimated_lines": lines,
                "max_lines": capacity["max_lines"],
                "overflow_threshold_lines": overflow_threshold,
                "target_words": capacity["target_words"],
                "soft_max_words": capacity["soft_max_words"],
                "text": text,
            })

    if not overflow_items:
        return texts_map

    lang = settings.get("language", "Portuguese") if settings else "Portuguese"
    system_instruction = (
        "You are an executive editor for PDF report text blocks. Rewrite only the provided "
        "overflowing text components so they fit their exact PDF slots. Preserve the most "
        "important metrics, conclusions, and analytical meaning. Return only valid JSON."
    )
    user_prompt = (
        f"Target Language: {lang}\n"
        "Each item below overflowed its assigned PDF text block. Condense each text to no more "
        "than its soft_max_words, and only go down to target_words if strictly necessary. "
        "Use complete sentences, no markdown, no bullet lists, and no ellipses. "
        "Return a JSON object mapping each id to the condensed string.\n\n"
        f"Overflowing Text Blocks:\n{json.dumps(overflow_items, indent=2, ensure_ascii=False)}"
    )

    for attempt in range(3):
        try:
            response = call_llm(provider, model_name, system_instruction, user_prompt)
            payload = json.loads(extract_json_payload(response, "{", "}"))
            for key, value in payload.items():
                if key in texts_map and isinstance(value, str) and value.strip():
                    texts_map[key] = value.strip()
            return texts_map
        except Exception as e:
            print(f"DEBUG: text condensation attempt {attempt + 1} failed: {str(e)}")
            time.sleep(2)

    return texts_map


def generate_report_title(all_metadata: str, prompt: str, artifacts: list, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None) -> str:
    system_instruction = load_prompt("GENERATE_REPORT_TITLE.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    user_prompt = (
        f"Target Language: {lang}\n"
        f"User Goal:\n{prompt}\n\n"
        f"Data Artifacts:\n{all_metadata}\n\n"
        f"Selected Artifacts:\n{json.dumps(artifacts, indent=2, ensure_ascii=False)}"
    )

    try:
        title = call_llm(provider, model_name, system_instruction, user_prompt).strip()
        title = strip_code_fence(title).strip().strip('"').strip("'")
        title = " ".join(title.split())
        if title:
            return title
    except Exception as e:
        print(f"DEBUG: title generation failed: {str(e)}")

    return fallback_report_title(prompt)


def fallback_report_title(prompt: str) -> str:
    clean_prompt = " ".join((prompt or "").split())
    if not clean_prompt:
        return "Relatório Analítico"
    return clean_prompt

def assemble_script(
    components: dict,
    template: list,
    files_map: dict,
    output_pdf_path: str,
    settings: dict = None,
    page_format: tuple[float, float] | None = None,
    include_title: bool = True,
    normalize_layout: bool = True,
) -> str:
    template = sorted(
        normalize_template(template) if normalize_layout else [item.copy() for item in template or []],
        key=lambda item: (item.get("page", 1), item.get("y", 0), item.get("x", 0))
    )
    # Default settings if none provided
    if not settings:
        settings = {
            "title": "Relatório Analítico",
            "font": "Arial",
            "font_size": 10,
            "alignment": "J",
            "line_height": 5
        }
    
    # fpdf2 uses standard names like 'helvetica', 'times', 'courier'
    font_family = settings.get('font', 'Arial').lower()
    if font_family == 'arial': font_family = 'helvetica'
    
    script = [
        "import os", "os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')",
        "import json",
        "import pandas as pd", "import numpy as np", "import matplotlib as mpl", "import matplotlib.pyplot as plt", "import seaborn as sns",
        "from fpdf import FPDF, XPos, YPos", "import tempfile", "import textwrap", "",
        "CHART_TITLE_FONTSIZE = 9",
        "CHART_TITLE_FONTWEIGHT = 'bold'",
        "CHART_TITLE_PAD = 6",
        "CHART_LABEL_FONTSIZE = 7",
        "CHART_TICK_FONTSIZE = 6",
        "CHART_LEGEND_FONTSIZE = 6",
        "CHART_TABLE_FONTSIZE = 6",
        "sns.set_theme(style='whitegrid')",
        "mpl.rcParams.update({",
        "    'axes.titlesize': CHART_TITLE_FONTSIZE,",
        "    'axes.titleweight': CHART_TITLE_FONTWEIGHT,",
        "    'axes.labelsize': CHART_LABEL_FONTSIZE,",
        "    'xtick.labelsize': CHART_TICK_FONTSIZE,",
        "    'ytick.labelsize': CHART_TICK_FONTSIZE,",
        "    'legend.fontsize': CHART_LEGEND_FONTSIZE,",
        "})",
        "",
        "def _install_standard_chart_style():",
        "    from matplotlib.axes import Axes",
        "    original_set_title = Axes.set_title",
        "    original_set_xlabel = Axes.set_xlabel",
        "    original_set_ylabel = Axes.set_ylabel",
        "    original_tick_params = Axes.tick_params",
        "    original_legend = Axes.legend",
        "    original_plt_legend = plt.legend",
        "    def set_title(self, label, fontdict=None, loc=None, pad=None, *, y=None, **kwargs):",
        "        fontdict = dict(fontdict or {})",
        "        fontdict['fontsize'] = CHART_TITLE_FONTSIZE",
        "        fontdict['fontweight'] = CHART_TITLE_FONTWEIGHT",
        "        kwargs['fontsize'] = CHART_TITLE_FONTSIZE",
        "        kwargs['fontweight'] = CHART_TITLE_FONTWEIGHT",
        "        return original_set_title(self, label, fontdict=fontdict, loc=loc, pad=CHART_TITLE_PAD if pad is None else pad, y=y, **kwargs)",
        "    def set_xlabel(self, xlabel, fontdict=None, labelpad=None, loc=None, **kwargs):",
        "        fontdict = dict(fontdict or {})",
        "        fontdict['fontsize'] = CHART_LABEL_FONTSIZE",
        "        fontdict['fontweight'] = 'normal'",
        "        kwargs['fontsize'] = CHART_LABEL_FONTSIZE",
        "        kwargs['fontweight'] = 'normal'",
        "        return original_set_xlabel(self, xlabel, fontdict=fontdict, labelpad=labelpad, loc=loc, **kwargs)",
        "    def set_ylabel(self, ylabel, fontdict=None, labelpad=None, loc=None, **kwargs):",
        "        fontdict = dict(fontdict or {})",
        "        fontdict['fontsize'] = CHART_LABEL_FONTSIZE",
        "        fontdict['fontweight'] = 'normal'",
        "        kwargs['fontsize'] = CHART_LABEL_FONTSIZE",
        "        kwargs['fontweight'] = 'normal'",
        "        return original_set_ylabel(self, ylabel, fontdict=fontdict, labelpad=labelpad, loc=loc, **kwargs)",
        "    def tick_params(self, axis='both', **kwargs):",
        "        kwargs['labelsize'] = CHART_TICK_FONTSIZE",
        "        return original_tick_params(self, axis=axis, **kwargs)",
        "    def legend(self, *args, **kwargs):",
        "        kwargs['fontsize'] = CHART_LEGEND_FONTSIZE",
        "        return original_legend(self, *args, **kwargs)",
        "    def plt_legend(*args, **kwargs):",
        "        kwargs['fontsize'] = CHART_LEGEND_FONTSIZE",
        "        return original_plt_legend(*args, **kwargs)",
        "    Axes.set_title = set_title",
        "    Axes.set_xlabel = set_xlabel",
        "    Axes.set_ylabel = set_ylabel",
        "    Axes.tick_params = tick_params",
        "    Axes.legend = legend",
        "    plt.legend = plt_legend",
        "",
        "_install_standard_chart_style()",
        "",
        "def _safe_pdf_text(value):",
        "    replacements = {",
        "        '\\u2013': '-', '\\u2014': '-', '\\u2015': '-',",
        "        '\\u2018': \"'\", '\\u2019': \"'\", '\\u201c': '\"', '\\u201d': '\"',",
        "        '\\u2022': '-', '\\u00a0': ' ', '\\u2026': '...'",
        "    }",
        "    text = str(value or '')",
        "    for source, target in replacements.items():",
        "        text = text.replace(source, target)",
        "    return text.encode('latin-1', 'replace').decode('latin-1')",
        "",
        "def _wrap_lines(text, max_chars):",
        "    lines = []",
        "    for paragraph in str(text).splitlines() or ['']:",
        "        wrapped = textwrap.wrap(paragraph.strip(), width=max_chars) or ['']",
        "        lines.extend(wrapped)",
        "    return lines",
        "",
        "def _sentence_chunks(text):",
        "    normalized = ' '.join(str(text).split())",
        "    chunks, current = [], []",
        "    for token in normalized.split(' '):",
        "        current.append(token)",
        "        if token.rstrip().endswith(('.', '!', '?', ';')):",
        "            chunks.append(' '.join(current))",
        "            current = []",
        "    if current:",
        "        chunks.append(' '.join(current))",
        "    return chunks or [normalized]",
        "",
        "def _fit_text_to_box(text, w, h, font_size, line_height):",
        "    max_chars = max(14, int(w * 0.55 * (10 / max(font_size, 1))))",
        "    max_lines = max(1, int(h / max(line_height, 1)))",
        "    if len(_wrap_lines(text, max_chars)) <= max_lines:",
        "        return text",
        "    selected = []",
        "    for sentence in _sentence_chunks(text):",
        "        candidate = ' '.join(selected + [sentence]).strip()",
        "        if not candidate:",
        "            continue",
        "        if len(_wrap_lines(candidate, max_chars)) <= max_lines:",
        "            selected.append(sentence)",
        "        elif selected:",
        "            break",
        "        else:",
        "            words = sentence.split()",
        "            compact = []",
        "            for word in words:",
        "                candidate = ' '.join(compact + [word])",
        "                if len(_wrap_lines(candidate, max_chars)) > max_lines:",
        "                    break",
        "                compact.append(word)",
        "            selected.append(' '.join(compact))",
        "            break",
        "    fitted = ' '.join(selected).strip()",
        "    return fitted",
        "",
        "def _write_report_title(pdf, title, font_family):",
        "    clean_title = ' '.join(_safe_pdf_text(title or 'Relatório Analítico').split())",
        "    for size in range(16, 10, -1):",
        "        max_chars = max(28, int(58 * (16 / size)))",
        "        lines = textwrap.wrap(clean_title, width=max_chars) or [clean_title]",
        "        if len(lines) <= 2:",
        "            pdf.set_font(font_family, 'B', size)",
        "            pdf.multi_cell(0, 8, '\\n'.join(lines), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')",
        "            return",
        "    pdf.set_font(font_family, 'B', 11)",
        "    pdf.multi_cell(0, 7, '\\n'.join(textwrap.wrap(clean_title, width=84)), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')",
        "",
        "def render_text_block(pdf, text, x, y, w, h, title=None):",
        f"    font_size = {settings.get('font_size', 10)}",
        f"    line_height = {settings.get('line_height', 5)}",
        "    text = _safe_pdf_text(text)",
        "    title = _safe_pdf_text(title) if title else None",
        "    body_height = h",
        f"    pdf.set_xy(x, y)",
        f"    if title:",
        f"        pdf.set_font('{font_family}', 'B', font_size)",
        f"        pdf.cell(w, 7, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)",
        f"        body_height = max(1, h - 8)",
        f"        pdf.set_xy(x, pdf.get_y())",
        f"    ",
        f"    fitted_text = _fit_text_to_box(text, w, body_height, font_size, line_height)",
        f"    pdf.set_font('{font_family}', '', font_size)",
        f"    pdf.multi_cell(w, line_height, fitted_text, align='{settings.get('alignment', 'J')}')",
        ""
    ]
    for comp_id, func_code in components.items():
        script.append(func_code)
        script.append("")
    script.append("def generate_report():")
    script.append("    dataframes = {}")
    for original_name, path in files_map.items():
        line = dataframe_load_line(original_name, path, indent="    ")
        if line:
            script.append(line)

    if page_format:
        script.append(f"    pdf = FPDF(format=({float(page_format[0])}, {float(page_format[1])}))")
    else:
        script.append("    pdf = FPDF()")
    script.append("    pdf.set_auto_page_break(auto=False)")
    script.append("    current_page = 0")
    script.append("    component_manifest = []")
    script.append("    def ensure_page(page):")
    script.append("        nonlocal current_page")
    script.append("        while current_page < page:")
    script.append("            pdf.add_page()")
    script.append("            current_page += 1")
    if include_title:
        script.append("            if current_page == 1:")
        script.append(f"                _write_report_title(pdf, {json.dumps(settings.get('title') or 'Relatório Analítico')}, '{font_family}')")
        script.append("                pdf.ln(3)")
    else:
        script.append("            pass")
    script.append("    ensure_page(1)")
    for item in template:
        page = item.get("page", 1)
        script.append(f"    ensure_page({page})")
        script.append(f"    try:")
        script.append(f"        # Provide a default 'df' for backward compatibility with older prompts/scripts")
        script.append(f"        primary_df = list(dataframes.values())[0] if dataframes else None")
        script.append(f"        try:")
        script.append(f"            render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']}, df=primary_df)")
        script.append(f"        except TypeError:")
        script.append(f"            render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']})")
        script.append(f"        component_manifest.append({{'id': {json.dumps(item['id'])}, 'status': 'rendered'}})")
        script.append(f"    except Exception as e: ")
        script.append(f"        component_manifest.append({{'id': {json.dumps(item['id'])}, 'status': 'failed', 'error': str(e)}})")
        script.append(f"        print(f'Erro no componente {item['id']}: {{e}}')")
        script.append(f"        import traceback; traceback.print_exc()")
    script.append(f"    manifest_path = r'{output_pdf_path}.manifest.json'")
    script.append("    with open(manifest_path, 'w', encoding='utf-8') as manifest_file:")
    script.append("        json.dump({'components': component_manifest}, manifest_file, indent=2, ensure_ascii=False)")
    script.append("    failed_components = [item for item in component_manifest if item.get('status') != 'rendered']")
    script.append("    if failed_components:")
    script.append("        raise RuntimeError('Report component rendering failed: ' + ', '.join(item['id'] for item in failed_components))")
    script.append(f"    pdf.output(r'{output_pdf_path}')")
    script.append("")
    script.append("if __name__ == '__main__':")
    script.append("    generate_report()")
    return "\n".join(script)

def fix_script(current_script: str, error: str, all_metadata: str, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME) -> str:
    system_instruction = load_prompt("FIX_SCRIPT.md")
    user_prompt = f"Reported Error: {error}\n\nCurrent Failing Script:\n{current_script}\n\nData Artifacts:\n{all_metadata}"
    
    text = call_llm(provider, model_name, system_instruction, user_prompt)
    return strip_code_fence(text, "python")

def generate_exploration_script(all_metadata: str, query: str, files_map: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, history: list = None) -> str:
    system_instruction = load_prompt("EXPLORE_DATA.md")
    history_str = ""
    if history:
        history_str = "Conversation History:\n"
        for h in history: history_str += f"{h['role'].upper()}: {h['content']}\n"
    
    user_prompt = f"{history_str}\nData Artifacts:\n{all_metadata}\n\nUser Query: {query}"
    
    text = call_llm(provider, model_name, system_instruction, user_prompt)
    text = strip_code_fence(text, "python")
    
    # Injetar carregamento dos dataframes
    setup_code = [
        "import pandas as pd",
        "dataframes = {}"
    ]
    for original_name, path in files_map.items():
        line = dataframe_load_line(original_name, path)
        if line:
            setup_code.append(line)
    
    full_script = "\n".join(setup_code) + "\n\n" + text
    return full_script

def translate_workspace(session: dict, target_lang: str) -> dict:
    system_instruction = load_prompt("TRANSLATE_WORKSPACE.md")
    provider = session.get('provider', DEFAULT_PROVIDER)
    model_name = session.get('model_name')
    
    workspace_data = {
        "target_language": target_lang,
        "artifacts": session.get('artifacts', []),
        "components": session.get('components', {})
    }
    
    user_prompt = f"Workspace to Translate:\n{json.dumps(workspace_data, indent=2)}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = extract_json_payload(text, "{", "}")
        
        try:
            res = json.loads(text)
            if "artifacts" in res and "components" in res:
                return res
        except:
            time.sleep(2)
    return None

def refine_session_modular(session: dict, new_prompt: str) -> dict:
    provider = session.get('provider', DEFAULT_PROVIDER)
    model_name = session.get('model_name')
    system_instruction = load_prompt("REFINE_MODULAR.md")
    settings = session.get('settings', {})
    lang = settings.get('language', 'Portuguese')
    
    artifacts_str = json.dumps([{"id": a["id"], "name": a["indicator"]} for a in session.get("artifacts", [])], indent=2)
    user_prompt = f"Target Language: {lang}\nUser Feedback/Prompt: \"{new_prompt}\"\n\nCurrent List of Indicators:\n{artifacts_str}"
    
    target = call_llm(provider, model_name, system_instruction, user_prompt).strip()
    
    all_metadata = json.dumps([f['artifact'] for f in session['files'] if f.get('artifact')], indent=2)

    if target in [a["id"] for a in session.get("artifacts", [])]:
        artifact = next(a for a in session["artifacts"] if a["id"] == target)
        refine_instruction = f"Target Language: {lang}\nUpdate the FUNCTION render_{target} based on: {new_prompt}\n\nCurrent Code:\n{session['components'][target]}\n\nData Artifacts:\n{all_metadata}"
        
        new_func = call_llm(provider, model_name, "Expert Python Developer specialized in report components.", refine_instruction).strip()
        new_func = strip_code_fence(new_func, "python")
        session['components'][target] = new_func
        session['needs_script_update'] = True
    elif target == "LAYOUT":
        refine_instruction = f"Target Language: {lang}\nUpdate the layout JSON based on: {new_prompt}\n\nCurrent Layout: {json.dumps(session['template'], indent=2)}"
        res = call_llm(provider, model_name, "Expert Information Designer specialized in JSON layouts.", refine_instruction).strip()
        res = extract_json_payload(res, "[", "]")
        try:
            session['template'] = normalize_template(json.loads(res))
            session['needs_script_update'] = True
        except: pass
    elif target == "SETTINGS":
        refine_instruction = f"Update the report settings JSON based on: {new_prompt}\n\nCurrent Settings: {json.dumps(settings, indent=2)}\n\nFormat: {{\"title\": str, \"font\": str, \"font_size\": int, \"alignment\": \"L\"|\"C\"|\"R\"|\"J\", \"line_height\": int, \"language\": str}}"
        res = call_llm(provider, model_name, "Expert Designer specialized in document settings.", refine_instruction).strip()
        res = extract_json_payload(res, "{", "}")
        try:
            new_settings = json.loads(res)
            current_settings = session.get('settings', {
                "title": "Relatório Analítico",
                "font": "Arial",
                "font_size": 10,
                "alignment": "J",
                "line_height": 5,
                "language": "Portuguese"
            })
            
            old_lang = current_settings.get('language')
            new_lang = new_settings.get('language')
            
            current_settings.update(new_settings)
            session['settings'] = current_settings
            session['needs_script_update'] = True
            
            if new_lang and old_lang and new_lang != old_lang:
                # Trigger workspace translation
                translated = translate_workspace(session, new_lang)
                if translated:
                    session['artifacts'] = translated['artifacts']
                    session['components'] = translated['components']
                    # Also translate title if it was default
                    if "Relatório" in current_settings['title'] or "Report" in current_settings['title']:
                         # Simple heuristic or let LLM decide title in prompt
                         pass
        except: pass
    else:
        session['needs_full_regeneration'] = True
    return session
