from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import List
import shutil
import os
import sys
import uuid
import time
import traceback
import json

# Permite rodar o script diretamente da raiz do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.data_analyzer import extract_metadata
from app.utils.layout import normalize_template
from app.utils.template_library import (
    delete_layout_template,
    fit_artifacts_to_template,
    load_layout_templates,
    select_layout_template,
    template_constraints,
    upsert_layout_template,
)
from app.services.llm_service import (
    generate_artifacts, 
    generate_template, 
    generate_component_code, 
    assemble_script, 
    refine_session_modular, 
    fix_script, 
    generate_exploration_script,
    calculate_metric_value,
    generate_all_texts,
    generate_report_title,
    translate_workspace
)
from app.services.executor_service import execute_script
from app.services.session_manager import create_session, get_session, update_session, add_log

app = FastAPI(title="AI PDF Report Generator v3")

UPLOAD_DIR = "workspace/uploads"
SCRIPT_DIR = "workspace/scripts"
REPORT_DIR = "workspace/reports"
VISUAL_TYPES = {'graphic', 'bar', 'line', 'pie', 'scatter', 'table', 'chart'}
DEFAULT_SETTINGS = {
    "title": "",
    "font": "Arial",
    "font_size": 10,
    "alignment": "L",
    "line_height": 5,
    "language": "Portuguese"
}
for d in [UPLOAD_DIR, SCRIPT_DIR, REPORT_DIR]:
    os.makedirs(d, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/templates", response_class=HTMLResponse)
async def read_template_editor():
    with open("static/templates.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/session/{job_id}", response_class=HTMLResponse)
async def read_session_workspace(job_id: str):
    require_session(job_id)
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


def require_session(job_id: str) -> dict:
    session = get_session(job_id)
    if not session:
        raise HTTPException(status_code=404)
    return session


def save_uploaded_files(files: List[UploadFile]) -> list:
    saved_files = []
    for file in files:
        file_ext = os.path.splitext(file.filename)[1]
        data_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{file_ext}")
        with open(data_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append({
            "original_filename": file.filename,
            "path": data_path,
            "artifact": None
        })
    return saved_files


def build_files_map(session: dict) -> dict:
    return {f['original_filename']: os.path.abspath(f['path']) for f in session.get('files', [])}


def metadata_json(session: dict) -> str:
    return json.dumps([f['artifact'] for f in session.get('files', []) if f.get('artifact')], indent=2)


def text_component_code(artifact: dict, text_content: str) -> str:
    safe_text = json.dumps(text_content)
    safe_title = json.dumps(artifact.get('indicator', ''))
    return (
        f"def render_{artifact['id']}(pdf, dataframes, x, y, w, h):\n"
        f"    render_text_block(pdf, {safe_text}, x, y, w, h, title={safe_title})"
    )


def run_pipeline(job_id: str):
    session = get_session(job_id)
    provider = session.get('provider')
    model_name = session.get('model_name')
    files_map = build_files_map(session)
    
    try:
        # 1. Metadados
        files = session.get('files', [])
        any_new_artifact = False
        for i, file_info in enumerate(files):
            if not file_info.get('artifact'):
                artifact = extract_metadata(file_info['path'], file_info['original_filename'])
                files[i]['artifact'] = artifact
                any_new_artifact = True
        if any_new_artifact: update_session(job_id, {"files": files})
        all_metadata = metadata_json({"files": files})

        # 2. Indicadores (Artifacts)
        artifacts = session.get('artifacts', [])
        settings = session.get('settings', {})
        selected_template_id = settings.get('selected_template_id')
        selected_template = None

        if not artifacts or any_new_artifact:
            max_artifacts_retries = 3
            if selected_template_id:
                selected_template = select_layout_template([], selected_template_id)
            for attempt in range(max_artifacts_retries):
                add_log(job_id, f"Planejando indicadores (Metric, Graphic, Text) - T{attempt+1}...")
                artifacts = generate_artifacts(
                    all_metadata,
                    session.get('current_goal', session['initial_prompt']),
                    provider,
                    model_name,
                    settings=settings,
                    template_context=template_constraints(selected_template) if selected_template else None
                )
                if isinstance(artifacts, list) and len(artifacts) > 0:
                    break
                time.sleep(2)

        visible_artifacts = [a for a in artifacts if a.get('type') in VISUAL_TYPES or a.get('type') == 'text']
        selected_template = select_layout_template(visible_artifacts, selected_template_id)
        if selected_template:
            selected_template_id = selected_template.get("id")
            settings["selected_template_id"] = selected_template_id
            artifacts = fit_artifacts_to_template(artifacts, selected_template)
            update_session(job_id, {"artifacts": artifacts, "settings": settings})
        elif not session.get('artifacts') or any_new_artifact:
            update_session(job_id, {"artifacts": artifacts})

        if not settings.get("title"):
            add_log(job_id, "Gerando título do relatório...")
            title = generate_report_title(
                all_metadata,
                session.get('current_goal', session['initial_prompt']),
                artifacts,
                provider,
                model_name,
                settings=settings
            )
            settings["title"] = title
            update_session(job_id, {"settings": settings})

        # 3. Cálculo de Métricas (Macro-tipo 1)
        metrics_store = session.get('calculated_metrics', {})
        metric_artifacts = [a for a in artifacts if a.get('type') == 'metric']
        for art in metric_artifacts:
            if art['id'] not in metrics_store or any_new_artifact:
                add_log(job_id, f"Calculando métrica: {art['indicator']}...")
                val = calculate_metric_value(all_metadata, art, files_map, provider, model_name)
                metrics_store[art['id']] = val
        update_session(job_id, {"calculated_metrics": metrics_store})

        # 4. Layout (Template)
        visible_artifacts = [a for a in artifacts if a.get('type') in VISUAL_TYPES or a.get('type') == 'text']
        template = session.get('template', [])
        if not template or len(template) != len(visible_artifacts):
            template_name = selected_template.get("name") if selected_template else "fallback"
            add_log(job_id, f"Aplicando template de layout: {template_name}...")
            template = generate_template(visible_artifacts, provider, model_name, template_id=selected_template_id)
            update_session(job_id, {"template": normalize_template(template)})

        # 5. Geração de Código
        components = session.get('components', {})
        
        # Gráficos
        graphic_artifacts = [a for a in artifacts if a.get('type') in VISUAL_TYPES]
        for art in graphic_artifacts:
            if art['id'] not in components:
                add_log(job_id, f"Gerando gráfico: {art['indicator']}...")
                code = generate_component_code(all_metadata, art, provider, model_name, calculated_metrics=metrics_store, settings=session.get('settings'))
                components[art['id']] = code

        # Textos (Chamada única para consistência)
        text_artifacts = [a for a in artifacts if a.get('type') == 'text']
        
        # Forçamos regeneração se: 
        # 1. Há novos dados (any_new_artifact)
        # 2. Algum texto falta em components
        # 3. Algum texto existente NÃO usa a nova função render_text_block (migração)
        needs_text_refresh = any_new_artifact or \
                             any(art['id'] not in components for art in text_artifacts) or \
                             any(art['id'] in components and "render_text_block" not in components[art['id']] for art in text_artifacts)

        if text_artifacts and needs_text_refresh:
            add_log(job_id, f"Gerando narrativas consistentes ({len(text_artifacts)})...")
            texts_map = generate_all_texts(all_metadata, text_artifacts, metrics_store, provider, model_name, settings=session.get('settings'))
            
            for art in text_artifacts:
                text_content = texts_map.get(art['id'], "Conteúdo não disponível.")
                components[art['id']] = text_component_code(art, text_content)
            
        update_session(job_id, {"components": components})
        run_script_generation(job_id)
        
    except Exception as e:
        traceback.print_exc()
        add_log(job_id, f"Erro na pipeline: {str(e)}")

def run_script_generation(job_id: str):
    session = get_session(job_id)
    add_log(job_id, "Montando script final modular...")
    
    script_path = os.path.join(SCRIPT_DIR, f"{job_id}.py")
    output_pdf_path = os.path.abspath(os.path.join(REPORT_DIR, f"{job_id}.pdf"))
    files_map = build_files_map(session)
    
    script_content = assemble_script(
        session['components'],
        normalize_template(session['template']),
        files_map,
        output_pdf_path,
        settings=session.get('settings')
    )
    
    update_session(job_id, {"script": script_content, "script_path": script_path, "pdf_path": output_pdf_path, "retry_count": 0})
    run_execution(job_id)

def run_execution(job_id: str):
    session = get_session(job_id)
    retries = session.get("retry_count", 0)
    max_retries = 5
    
    add_log(job_id, f"Renderizando PDF (Tentativa {retries + 1}/{max_retries})...")
    start_time = time.perf_counter()
    result = execute_script(session['script'], session['script_path'])
    duration = time.perf_counter() - start_time
    
    if result["success"]:
        new_version = session.get("pdf_version", 0) + 1
        update_session(job_id, {"pdf_version": new_version, "retry_count": 0, "status": "Concluído"})
        add_log(job_id, f"Relatório concluído em {duration:.1f}s")
    else:
        if retries < max_retries - 1:
            add_log(job_id, f"Erro detectado. Autocorreção iniciada...")
            all_metadata = metadata_json(session)
            fixed_code = fix_script(session['script'], result['stderr'], all_metadata, session.get('provider'), session.get('model_name'))
            update_session(job_id, {"script": fixed_code, "retry_count": retries + 1})
            run_execution(job_id)
        else:
            add_log(job_id, f"Falha após {max_retries} tentativas.")

@app.post("/api/sessions")
async def start_session(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    prompt: str = Form(...),
    provider: str = Form("gemini"),
    model_name: str = Form("models/gemini-3.1-flash-lite-preview")
):
    job_id = str(uuid.uuid4())
    session_files = save_uploaded_files(files)
    
    create_session(job_id, {
        "job_id": job_id,
        "status": "Iniciando...",
        "logs": ["Iniciando pipeline v3.0 (Multi-data)..."],
        "files": session_files,
        "initial_prompt": prompt,
        "current_goal": prompt,
        "refinement_history": [],
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
        "settings": DEFAULT_SETTINGS.copy(),
        "script": "",
        "provider": provider,
        "model_name": model_name,
        "pdf_version": 0,
        "retry_count": 0,
        "explore_history": []
    })
    
    background_tasks.add_task(run_pipeline, job_id)
    return {"job_id": job_id}

@app.post("/api/sessions/{job_id}/files")
async def add_files_to_session(
    job_id: str,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...)
):
    session = require_session(job_id)
    
    session_files = session.get("files", [])
    session_files.extend(save_uploaded_files(files))
    
    update_session(job_id, {"files": session_files})
    add_log(job_id, f"Adicionados {len(files)} novos arquivos.")
    background_tasks.add_task(run_pipeline, job_id)
    return {"status": "OK"}

@app.get("/api/sessions/{job_id}")
async def get_session_status(job_id: str):
    return require_session(job_id)

@app.post("/api/sessions/{job_id}/chat")
async def chat_refinement(job_id: str, background_tasks: BackgroundTasks, prompt: str = Form(...)):
    session = require_session(job_id)
    
    history = session.get('refinement_history', [])
    history.append(prompt)
    full_current_goal = session['initial_prompt'] + "\nRefinement History:\n- " + "\n- ".join(history)
    
    add_log(job_id, f"Processando refinamento modular: {prompt}")
    new_session_state = refine_session_modular(session, prompt)
    new_session_state.update({
        "refinement_history": history,
        "current_goal": full_current_goal
    })
    
    if new_session_state.get('needs_full_regeneration'):
        add_log(job_id, "Pedido global detectado. Reiniciando pipeline com novos requisitos...")
        update_session(job_id, {
            "artifacts": [], "template": [], "components": {}, "calculated_metrics": {},
            "refinement_history": history, "current_goal": full_current_goal
        })
        background_tasks.add_task(run_pipeline, job_id)
    else:
        update_session(job_id, new_session_state)
        if new_session_state.get('needs_script_update'):
            background_tasks.add_task(run_script_generation, job_id)
    
    return {"status": "OK"}

@app.put("/api/sessions/{job_id}/artifacts")
async def update_artifacts_manually(job_id: str, background_tasks: BackgroundTasks, payload: dict = Body(...)):
    session = require_session(job_id)
    
    new_artifacts = payload.get("artifacts", [])
    new_components = payload.get("components", {})
    old_artifacts = session.get('artifacts', [])
    current_components = session.get('components', {})
    # Detecção de mudanças para invalidar cache de código
    manual_override_ids = list(new_components.keys())

    for new_art in new_artifacts:
        # Garante que o tipo nunca fique vazio (fallback para text se sumir)
        if not new_art.get('type'):
            new_art['type'] = 'graphic' # Fallback seguro

        old_art = next((a for a in old_artifacts if a['id'] == new_art['id']), None)
        if old_art:
            changed = (
                old_art.get('type') != new_art.get('type') or 
                old_art.get('columns') != new_art.get('columns') or
                old_art.get('indicator') != new_art.get('indicator') or
                old_art.get('reason') != new_art.get('reason')
            )
            # SÓ deletamos o código para regenerar se houve mudança E se o usuário NÃO enviou um código manual novo agora
            if changed and new_art['id'] not in manual_override_ids:
                if new_art['id'] in current_components:
                    add_log(job_id, f"Mudança detectada em {new_art['id']}. Agendando regeneração...")
                    del current_components[new_art['id']]

    # Sincroniza componentes manuais (sobrescreve o que quer que estivesse lá)
    current_components.update(new_components)


    active_ids = [a['id'] for a in new_artifacts]
    current_components = {id: code for id, code in current_components.items() if id in active_ids}
    
    update_session(job_id, {
        "artifacts": new_artifacts, "components": current_components, "template": []
    })
    
    background_tasks.add_task(run_pipeline, job_id)
    return {"status": "OK"}

@app.put("/api/sessions/{job_id}/template")
async def update_template_manually(job_id: str, background_tasks: BackgroundTasks, template: list = Body(...)):
    session = require_session(job_id)
    
    update_session(job_id, {"template": normalize_template(template)})
    add_log(job_id, "Layout visual atualizado pelo usuário. Re-renderizando...")
    background_tasks.add_task(run_script_generation, job_id)
    return {"status": "OK"}

@app.put("/api/sessions/{job_id}/settings")
async def update_settings(job_id: str, background_tasks: BackgroundTasks, settings: dict = Body(...)):
    session = require_session(job_id)
    
    old_lang = session.get('settings', {}).get('language')
    new_lang = settings.get('language')
    old_template_id = session.get('settings', {}).get('selected_template_id')
    new_template_id = settings.get('selected_template_id')
    
    if old_lang and new_lang and old_lang != new_lang:
        add_log(job_id, f"Mudança de idioma detectada: {old_lang} -> {new_lang}. Traduzindo workspace...")
        translated = translate_workspace(session, new_lang)
        if translated:
            update_session(job_id, {
                "artifacts": translated['artifacts'],
                "components": translated['components'],
                "settings": settings
            })
        else:
            update_session(job_id, {"settings": settings})
    else:
        update_session(job_id, {"settings": settings})
    
    if new_template_id and old_template_id != new_template_id:
        add_log(job_id, "Template de layout alterado. Regenerando relatório para ajustar componentes...")
        update_session(job_id, {
            "settings": settings,
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {}
        })
        background_tasks.add_task(run_pipeline, job_id)
    else:
        add_log(job_id, "Configurações do relatório atualizadas. Re-renderizando...")
        background_tasks.add_task(run_script_generation, job_id)
    return {"status": "OK"}


@app.get("/api/layout-templates")
async def list_layout_templates():
    return {"templates": load_layout_templates()}


@app.post("/api/layout-templates")
async def create_layout_template(payload: dict = Body(...)):
    return upsert_layout_template(payload)


@app.put("/api/layout-templates/{template_id}")
async def update_layout_template(template_id: str, payload: dict = Body(...)):
    payload["id"] = template_id
    return upsert_layout_template(payload)


@app.delete("/api/layout-templates/{template_id}")
async def remove_layout_template(template_id: str):
    if not delete_layout_template(template_id):
        raise HTTPException(status_code=404)
    return {"status": "OK"}

@app.get("/api/sessions/{job_id}/pdf")
async def get_pdf(job_id: str):
    session = get_session(job_id)
    if not session or not os.path.exists(session.get('pdf_path', '')): raise HTTPException(status_code=404)
    return FileResponse(session['pdf_path'], media_type="application/pdf")

@app.post("/api/sessions/{job_id}/explore")
async def explore_data(job_id: str, prompt: str = Form(...)):
    session = require_session(job_id)
    
    history = session.get('explore_history', [])
    all_metadata = metadata_json(session)
    files_map = build_files_map(session)
    
    script_content = generate_exploration_script(
        all_metadata, prompt, files_map, session.get('provider'), session.get('model_name'), history=history
    )
    script_path = os.path.join(SCRIPT_DIR, f"explore_{job_id}.py")
    result = execute_script(script_content, script_path)
    
    answer = result["stdout"] if result["success"] else f"Erro: {result['stderr']}"
    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": answer})
    update_session(job_id, {"explore_history": history})
    return {"answer": answer}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["app", "static"])
