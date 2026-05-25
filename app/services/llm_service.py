import google.generativeai as genai
import os
import json
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
    "gemini": "models/gemini-3.1-flash-lite-preview",
}
DEFAULT_MODEL_NAME = DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER]


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

def get_gemini_model(model_name="models/gemini-3.1-flash-lite-preview"):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)

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
                model = get_gemini_model(model_name)
                response = model.generate_content([system_instruction, user_prompt])
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

def generate_component_code(all_metadata: str, artifact: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, calculated_metrics: dict = None, preceding_text: str = None, settings: dict = None) -> str:
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
        
    user_prompt = "\n\n".join(context)
    
    text = call_llm(provider, model_name, system_instruction, user_prompt)
    return strip_code_fence(text, "python")

def generate_all_texts(all_metadata: str, text_artifacts: list, calculated_metrics: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, settings: dict = None) -> dict:
    system_instruction = load_prompt("GENERATE_ALL_TEXTS.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    user_prompt = f"Target Language: {lang}\nText Artifacts:\n{json.dumps(text_artifacts, indent=2)}\n\nCalculated Metrics:\n{json.dumps(calculated_metrics, indent=2)}\n\nData Artifacts:\n{all_metadata}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = extract_json_payload(text, "{", "}")

        try:
            return json.loads(text)
        except:
            time.sleep(2)
    return {}

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
            return title[:80]
    except Exception as e:
        print(f"DEBUG: title generation failed: {str(e)}")

    return fallback_report_title(prompt)


def fallback_report_title(prompt: str) -> str:
    clean_prompt = " ".join((prompt or "").split())
    if not clean_prompt:
        return "Relatório Analítico"
    return clean_prompt[:80]

def assemble_script(components: dict, template: list, files_map: dict, output_pdf_path: str, settings: dict = None) -> str:
    template = sorted(
        normalize_template(template),
        key=lambda item: (item.get("page", 1), item.get("y", 0), item.get("x", 0))
    )
    # Default settings if none provided
    if not settings:
        settings = {
            "title": "Relatório Analítico",
            "font": "Arial",
            "font_size": 10,
            "alignment": "L",
            "line_height": 5
        }
    
    # fpdf2 uses standard names like 'helvetica', 'times', 'courier'
    font_family = settings.get('font', 'Arial').lower()
    if font_family == 'arial': font_family = 'helvetica'
    
    script = [
        "import os", "os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')",
        "import pandas as pd", "import numpy as np", "import matplotlib.pyplot as plt", "import seaborn as sns",
        "from fpdf import FPDF, XPos, YPos", "import tempfile", "import textwrap", "",
        "sns.set_theme(style='whitegrid')", "",
        "def _fit_text_to_box(text, w, h, font_size, line_height):",
        "    max_chars = max(12, int(w * 0.55))",
        "    max_lines = max(1, int(h / max(line_height, 1)))",
        "    lines = []",
        "    for paragraph in str(text).splitlines() or ['']:",
        "        wrapped = textwrap.wrap(paragraph, width=max_chars) or ['']",
        "        lines.extend(wrapped)",
        "    if len(lines) > max_lines:",
        "        lines = lines[:max_lines]",
        "        lines[-1] = lines[-1].rstrip()[:max(0, max_chars - 3)] + '...'",
        "    return '\\n'.join(lines)",
        "",
        "def render_text_block(pdf, text, x, y, w, h, title=None):",
        f"    font_size = {settings.get('font_size', 10)}",
        f"    line_height = {settings.get('line_height', 5)}",
        "    body_height = h",
        f"    pdf.set_xy(x, y)",
        f"    if title:",
        f"        pdf.set_font('{font_family}', 'B', font_size)",
        f"        pdf.cell(w, 7, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)",
        f"        body_height = max(1, h - 8)",
        f"        pdf.set_xy(x, pdf.get_y())",
        f"    ",
        f"    pdf.set_font('{font_family}', '', font_size)",
        f"    fitted_text = _fit_text_to_box(text, w, body_height, font_size, line_height)",
        f"    pdf.multi_cell(w, line_height, fitted_text, align='{settings.get('alignment', 'L')}')",
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

    script.append("    pdf = FPDF()")
    script.append("    pdf.set_auto_page_break(auto=False)")
    script.append("    current_page = 0")
    script.append("    def ensure_page(page):")
    script.append("        nonlocal current_page")
    script.append("        while current_page < page:")
    script.append("            pdf.add_page()")
    script.append("            current_page += 1")
    script.append("            if current_page == 1:")
    script.append(f"                pdf.set_font('{font_family}', 'B', 16)")
    script.append(f"                pdf.cell(0, 10, {json.dumps(settings.get('title') or 'Relatório Analítico')}, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')")
    script.append("                pdf.ln(5)")
    script.append("    ensure_page(1)")
    for item in template:
        page = item.get("page", 1)
        script.append(f"    ensure_page({page})")
        script.append(f"    try:")
        script.append(f"        # Provide a default 'df' for backward compatibility with older prompts/scripts")
        script.append(f"        primary_df = list(dataframes.values())[0] if dataframes else None")
        script.append(f"        render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']}, df=primary_df)")
        script.append(f"    except TypeError: # Fallback if function doesn't accept 'df' keyword argument")
        script.append(f"        try: render_{item['id']}(pdf, dataframes, {item['x']}, {item['y']}, {item['w']}, {item['h']})")
        script.append(f"        except Exception as e: ")
        script.append(f"            print(f'Erro no componente {item['id']}: {{e}}')")
        script.append(f"            import traceback; traceback.print_exc()")
        script.append(f"    except Exception as e: ")
        script.append(f"        print(f'Erro no componente {item['id']}: {{e}}')")
        script.append(f"        import traceback; traceback.print_exc()")
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
                "alignment": "L",
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
