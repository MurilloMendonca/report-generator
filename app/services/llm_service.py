import google.generativeai as genai
import os
import json
import time
from dotenv import load_dotenv
from google.api_core import exceptions
from openai import OpenAI

load_dotenv()

PROMPTS_DIR = "prompts"

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

def call_llm(provider: str, model_name: str, system_instruction: str, user_prompt: str, retries=3, delay=10):
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

def generate_artifacts(all_metadata: str, prompt: str, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview", settings: dict = None) -> list:
    system_instruction = load_prompt("GENERATE_ARTIFACTS.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    user_prompt = f"Target Language: {lang}\nData Artifacts:\n{all_metadata}\n\nUser's Initial Prompt: {prompt}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = text.strip()
        
        if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
        if "[" in text and "]" in text: text = text[text.find("["):text.rfind("]")+1]
            
        try:
            artifacts = json.loads(text)
            if isinstance(artifacts, list) and len(artifacts) > 0:
                return artifacts
        except:
            time.sleep(2)
            
    return []

def generate_template(artifacts: list, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview") -> list:
    from app.utils.template_library import BLUEPRINTS
    system_instruction = load_prompt("GENERATE_TEMPLATE.md").replace("{blueprints_json}", json.dumps(BLUEPRINTS, indent=2))
    
    artifacts_simple = [{"id": a["id"], "type": a["type"], "name": a["indicator"]} for a in artifacts]
    user_prompt = f"List of Indicators to Position:\n{json.dumps(artifacts_simple, indent=2)}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = text.strip()
        if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
        if "[" in text and "]" in text: text = text[text.find("["):text.rfind("]")+1]

        try:
            template = json.loads(text)
            if isinstance(template, list) and len(template) > 0:
                return template
        except:
            time.sleep(2)

    # Fallback vertical stack (A4 aware)
    # Starting at y=30 to leave space for Title (10mm) + Margin (15mm) + buffer
    total_h = 250 
    n = len(artifacts)
    h_per_item = min(50, total_h // n) if n > 0 else 50
    return [{"id": a["id"], "x": 10, "y": 30 + i*(h_per_item + 5), "w": 190, "h": h_per_item} for i, a in enumerate(artifacts)]

def calculate_metric_value(all_metadata: str, artifact: dict, files_map: dict, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview") -> any:
    """
    Generates and executes a script to calculate a metric value with automatic retries.
    """
    system_instruction = load_prompt("GENERATE_METRIC_VALUE.md")
    user_prompt = f"Target Metric:\n{json.dumps(artifact, indent=2)}\n\nData Artifacts:\n{all_metadata}"
    
    # Injetar carregamento dos dataframes para execução (com paths absolutos e raw strings)
    setup_code = ["import pandas as pd", "import json", "import os", "dataframes = {}"]
    for original_name, path in files_map.items():
        abs_path = os.path.abspath(path)
        ext = os.path.splitext(abs_path)[1].lower()
        if ext == '.csv': setup_code.append(f"dataframes['{original_name}'] = pd.read_csv(r'{abs_path}')")
        else: setup_code.append(f"dataframes['{original_name}'] = pd.read_excel(r'{abs_path}')")
    
    for attempt in range(3):
        try:
            code = call_llm(provider, model_name, system_instruction, f"{user_prompt}\n\nAttempt: {attempt+1}").strip()
            if "```python" in code: code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code: code = code.split("```")[1].split("```")[0].strip()

            full_script = "\n".join(setup_code) + "\n\n" + code
            
            from app.services.executor_service import execute_script
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
                result = execute_script(full_script, tmp.name)
                os.unlink(tmp.name) # Cleanup manual
                
                if result["success"]:
                    output = result["stdout"].strip()
                    if "{" in output:
                        json_str = output[output.rfind("{"):output.rfind("}")+1]
                        val = json.loads(json_str).get("value")
                        if val is not None and val != "N/A" and "Error" not in str(val):
                            return val
                
                # Se falhou, logamos o erro para debug (interno)
                print(f"DEBUG: Metric {artifact['id']} attempt {attempt+1} failed: {result['stderr']}")
        except Exception as e:
            print(f"DEBUG: Metric {artifact['id']} exception: {str(e)}")
            
        time.sleep(1)
        
    return "N/A"

def generate_component_code(all_metadata: str, artifact: dict, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview", calculated_metrics: dict = None, preceding_text: str = None, settings: dict = None) -> str:
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
    text = text.strip()
    if "```python" in text: text = text.split("```python")[1].split("```")[0].strip()
    elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
    return text

def generate_all_texts(all_metadata: str, text_artifacts: list, calculated_metrics: dict, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview", settings: dict = None) -> dict:
    system_instruction = load_prompt("GENERATE_ALL_TEXTS.md")
    lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
    user_prompt = f"Target Language: {lang}\nText Artifacts:\n{json.dumps(text_artifacts, indent=2)}\n\nCalculated Metrics:\n{json.dumps(calculated_metrics, indent=2)}\n\nData Artifacts:\n{all_metadata}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = text.strip()
        if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
        if "{" in text and "}" in text: text = text[text.find("{"):text.rfind("}")+1]

        try:
            return json.loads(text)
        except:
            time.sleep(2)
    return {}

def assemble_script(components: dict, template: list, files_map: dict, output_pdf_path: str, settings: dict = None) -> str:
    # Default settings if none provided
    if not settings:
        settings = {
            "title": "Relatório Analítico Inteligente",
            "font": "Arial",
            "font_size": 10,
            "alignment": "L",
            "line_height": 5
        }
    
    # fpdf2 uses standard names like 'helvetica', 'times', 'courier'
    font_family = settings.get('font', 'Arial').lower()
    if font_family == 'arial': font_family = 'helvetica'
    
    script = [
        "import pandas as pd", "import numpy as np", "import matplotlib.pyplot as plt", "import seaborn as sns",
        "from fpdf import FPDF", "import tempfile", "import os", "",
        "sns.set_theme(style='whitegrid')", "",
        f"def render_text_block(pdf, text, x, y, w, h, title=None):",
        f"    pdf.set_xy(x, y)",
        f"    if title:",
        f"        pdf.set_font('{font_family}', 'B', {settings.get('font_size', 10)})",
        f"        pdf.cell(w, 7, title, ln=True)",
        f"        pdf.set_xy(x, pdf.get_y())",
        f"    ",
        f"    pdf.set_font('{font_family}', '', {settings.get('font_size', 10)})",
        f"    pdf.multi_cell(w, {settings.get('line_height', 5)}, text, align='{settings.get('alignment', 'L')}')",
        ""
    ]
    for comp_id, func_code in components.items():
        script.append(func_code)
        script.append("")
    script.append("def generate_report():")
    script.append("    dataframes = {}")
    for original_name, path in files_map.items():
        ext = os.path.splitext(path)[1].lower()
        if ext == '.csv':
            script.append(f"    dataframes['{original_name}'] = pd.read_csv(r'{path}')")
        else:
            script.append(f"    dataframes['{original_name}'] = pd.read_excel(r'{path}')")

    script.append("    pdf = FPDF()")
    script.append("    pdf.add_page()")
    script.append("    pdf.set_auto_page_break(auto=True, margin=15)")
    script.append(f"    pdf.set_font('{font_family}', 'B', 16)")
    script.append(f"    pdf.cell(0, 10, {json.dumps(settings.get('title', 'Relatório Analítico Inteligente'))}, ln=True, align='C')")
    script.append("    pdf.ln(5)")
    for item in template:
        script.append(f"    try: ")
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

def fix_script(current_script: str, error: str, all_metadata: str, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview") -> str:
    system_instruction = load_prompt("FIX_SCRIPT.md")
    user_prompt = f"Reported Error: {error}\n\nCurrent Failing Script:\n{current_script}\n\nData Artifacts:\n{all_metadata}"
    
    text = call_llm(provider, model_name, system_instruction, user_prompt)
    text = text.strip()
    if "```python" in text: text = text.split("```python")[1].split("```")[0].strip()
    elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
    return text

def generate_exploration_script(all_metadata: str, query: str, files_map: dict, provider: str = "gemini", model_name: str = "models/gemini-3.1-flash-lite-preview", history: list = None) -> str:
    system_instruction = load_prompt("EXPLORE_DATA.md")
    history_str = ""
    if history:
        history_str = "Conversation History:\n"
        for h in history: history_str += f"{h['role'].upper()}: {h['content']}\n"
    
    user_prompt = f"{history_str}\nData Artifacts:\n{all_metadata}\n\nUser Query: {query}"
    
    text = call_llm(provider, model_name, system_instruction, user_prompt)
    text = text.strip()
    if "```python" in text: text = text.split("```python")[1].split("```")[0].strip()
    elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
    
    # Injetar carregamento dos dataframes
    setup_code = [
        "import pandas as pd",
        "dataframes = {}"
    ]
    for original_name, path in files_map.items():
        ext = os.path.splitext(path)[1].lower()
        if ext == '.csv':
            setup_code.append(f"dataframes['{original_name}'] = pd.read_csv('{path}')")
        else:
            setup_code.append(f"dataframes['{original_name}'] = pd.read_excel('{path}')")
    
    full_script = "\n".join(setup_code) + "\n\n" + text
    return full_script

def translate_workspace(session: dict, target_lang: str) -> dict:
    system_instruction = load_prompt("TRANSLATE_WORKSPACE.md")
    provider = session.get('provider', 'gemini')
    model_name = session.get('model_name')
    
    workspace_data = {
        "target_language": target_lang,
        "artifacts": session.get('artifacts', []),
        "components": session.get('components', {})
    }
    
    user_prompt = f"Workspace to Translate:\n{json.dumps(workspace_data, indent=2)}"
    
    for attempt in range(3):
        text = call_llm(provider, model_name, system_instruction, user_prompt)
        text = text.strip()
        if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
        if "{" in text and "}" in text: text = text[text.find("{"):text.rfind("}")+1]
        
        try:
            res = json.loads(text)
            if "artifacts" in res and "components" in res:
                return res
        except:
            time.sleep(2)
    return None

def refine_session_modular(session: dict, new_prompt: str) -> dict:
    provider = session.get('provider', 'gemini')
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
        if "```python" in new_func: new_func = new_func.split("```python")[1].split("```")[0].strip()
        elif "```" in new_func: new_func = new_func.split("```")[1].split("```")[0].strip()
        session['components'][target] = new_func
        session['needs_script_update'] = True
    elif target == "LAYOUT":
        refine_instruction = f"Target Language: {lang}\nUpdate the layout JSON based on: {new_prompt}\n\nCurrent Layout: {json.dumps(session['template'], indent=2)}"
        res = call_llm(provider, model_name, "Expert Information Designer specialized in JSON layouts.", refine_instruction).strip()
        if "```json" in res: res = res.split("```json")[1].split("```")[0].strip()
        if "[" in res and "]" in res: res = res[res.find("["):res.rfind("]")+1]
        try:
            session['template'] = json.loads(res)
            session['needs_script_update'] = True
        except: pass
    elif target == "SETTINGS":
        refine_instruction = f"Update the report settings JSON based on: {new_prompt}\n\nCurrent Settings: {json.dumps(settings, indent=2)}\n\nFormat: {{\"title\": str, \"font\": str, \"font_size\": int, \"alignment\": \"L\"|\"C\"|\"R\"|\"J\", \"line_height\": int, \"language\": str}}"
        res = call_llm(provider, model_name, "Expert Designer specialized in document settings.", refine_instruction).strip()
        if "```json" in res: res = res.split("```json")[1].split("```")[0].strip()
        if "{" in res and "}" in res: res = res[res.find("{"):res.rfind("}")+1]
        try:
            new_settings = json.loads(res)
            current_settings = session.get('settings', {
                "title": "Relatório Analítico Inteligente",
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
