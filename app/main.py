from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Body, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List
import shutil
import os
import sys
import uuid
import hmac
import mimetypes
from datetime import datetime, timezone

# Permite rodar o script diretamente da raiz do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.layout import normalize_template
from app.utils.template_library import (
    delete_layout_template,
    load_layout_templates,
    upsert_layout_template,
)
from app.services.llm_service import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    refine_session_modular, 
    generate_exploration_script,
    translate_workspace
)
from app.services.pipeline_service import (
    DEFAULT_EXPLORATORY_PROMPT,
    DEFAULT_SETTINGS,
    FILTERED_DIR,
    REPORT_DIR,
    SCRIPT_DIR,
    VISUAL_TYPES,
    apply_data_filter,
    build_files_map,
    clear_data_filter_state,
    has_data_filter_decision,
    metadata_json,
    run_pipeline,
    run_script_generation,
    should_consider_data_filter,
)
from app.services.session_manager import create_session, get_session, update_session, add_log, list_recent_sessions

app = FastAPI(title="Report Generator v0.3.6")

APP_ACCESS_SECRET = os.getenv("APP_ACCESS_SECRET") or os.getenv("PDF_REPORT_ACCESS_SECRET") or "change-me"

UPLOAD_DIR = "workspace/uploads"
for d in [UPLOAD_DIR, FILTERED_DIR, SCRIPT_DIR, REPORT_DIR]:
    os.makedirs(d, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")


def is_valid_access_token(token: str) -> bool:
    return bool(token) and hmac.compare_digest(token, APP_ACCESS_SECRET)


@app.middleware("http")
async def require_api_access(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/auth/login":
        token = request.headers.get("x-access-token", "")
        if not is_valid_access_token(token):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.post("/api/auth/login")
async def login(payload: dict = Body(...)):
    if not is_valid_access_token(payload.get("secret", "")):
        raise HTTPException(status_code=401, detail="Invalid access secret")
    return {"status": "OK"}


@app.get("/api/auth/check")
async def check_login():
    return {"status": "OK"}

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
            "size_bytes": os.path.getsize(data_path),
            "mime_type": file.content_type or mimetypes.guess_type(file.filename)[0],
            "artifact": None
        })
    return saved_files


def get_session_file(session: dict, file_index: int) -> dict:
    files = session.get("files", [])
    if file_index < 0 or file_index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    file_info = files[file_index]
    file_path = file_info.get("path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Stored file not found")
    return file_info


def session_file_variant(file_info: dict, variant: str = "original") -> dict:
    if variant == "preprocessed":
        variant_path = file_info.get("preprocessed_path") or file_info.get("filtered_path")
        if not variant_path or not os.path.exists(variant_path):
            raise HTTPException(status_code=404, detail="Preprocessed file not found")
        filename = file_info.get("original_filename") or os.path.basename(variant_path)
        stem, _ = os.path.splitext(filename)
        return {
            "path": variant_path,
            "filename": f"{stem}_preprocessed.csv",
            "mime_type": "text/csv",
        }

    return {
        "path": file_info["path"],
        "filename": file_info.get("original_filename") or os.path.basename(file_info["path"]),
        "mime_type": file_info.get("mime_type"),
    }


def file_response(file_info: dict, inline: bool = False, variant: str = "original") -> FileResponse:
    variant_info = session_file_variant(file_info, variant)
    filename = variant_info["filename"]
    file_path = variant_info["path"]
    media_type = variant_info.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline" if inline else "attachment"
    )


@app.post("/api/sessions")
async def start_session(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    prompt: str = Form(""),
    provider: str = Form(DEFAULT_PROVIDER),
    model_name: str = Form(DEFAULT_MODEL_NAME)
):
    job_id = str(uuid.uuid4())
    session_files = save_uploaded_files(files)
    now = datetime.now(timezone.utc).isoformat()
    user_prompt = (prompt or "").strip()
    effective_prompt = user_prompt or DEFAULT_EXPLORATORY_PROMPT
    
    create_session(job_id, {
        "job_id": job_id,
        "created_at": now,
        "updated_at": now,
        "status": "Iniciando...",
        "logs": ["Iniciando pipeline v3.0 (Multi-data)..."],
        "files": session_files,
        "initial_prompt": user_prompt or "Relatório exploratório dos dados",
        "current_goal": effective_prompt,
        "is_exploratory": not bool(user_prompt),
        "refinement_history": [],
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
        "settings": DEFAULT_SETTINGS.copy(),
        "script": "",
        "provider": provider,
        "model_name": model_name,
        "data_filter": {},
        "data_preprocessing": {},
        "pdf_version": 0,
        "retry_count": 0,
        "explore_history": []
    })
    
    background_tasks.add_task(run_pipeline, job_id)
    return {"job_id": job_id}


@app.get("/api/sessions")
async def recent_sessions():
    return {"sessions": list_recent_sessions()}

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

@app.get("/api/sessions/{job_id}/files/{file_index}/download")
async def download_session_file(job_id: str, file_index: int, variant: str = "original"):
    session = require_session(job_id)
    return file_response(get_session_file(session, file_index), variant=variant)

@app.get("/api/sessions/{job_id}/files/{file_index}/preview")
async def preview_session_file(job_id: str, file_index: int, variant: str = "original"):
    session = require_session(job_id)
    return file_response(get_session_file(session, file_index), inline=True, variant=variant)

@app.delete("/api/sessions/{job_id}/files/{file_index}")
async def delete_session_file(job_id: str, file_index: int, background_tasks: BackgroundTasks):
    session = require_session(job_id)
    file_info = get_session_file(session, file_index)
    files = session.get("files", [])
    removed = files.pop(file_index)

    try:
        os.remove(file_info["path"])
    except FileNotFoundError:
        pass
    for generated_path in [
        file_info.get("preprocessed_path"),
        file_info.get("filtered_path"),
    ]:
        if generated_path and generated_path != file_info.get("path"):
            try:
                os.remove(generated_path)
            except FileNotFoundError:
                pass

    update_payload = {
        "files": files,
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
    }
    if not files:
        update_payload.update({"status": "Aguardando arquivos", "script": "", "pdf_version": session.get("pdf_version", 0)})

    update_session(job_id, update_payload)
    add_log(job_id, f"Arquivo removido da sessão: {removed.get('original_filename', 'arquivo')}")
    if files:
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

    if should_consider_data_filter(prompt):
        add_log(job_id, f"Pré-processamento de dados solicitado: {prompt}")
        files = clear_data_filter_state(session.get("files", []))
        update_session(job_id, {
            "files": files,
            "data_filter": {},
            "data_preprocessing": {},
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
            "refinement_history": history,
            "current_goal": full_current_goal,
        })
        background_tasks.add_task(run_pipeline, job_id)
        return {"status": "OK"}
    
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
    provider = settings.pop("provider", None)
    model_name = settings.pop("model_name", None)
    
    old_lang = session.get('settings', {}).get('language')
    new_lang = settings.get('language')
    old_template_id = session.get('settings', {}).get('selected_template_id')
    new_template_id = settings.get('selected_template_id')
    session_updates = {"settings": settings}
    if provider:
        session_updates["provider"] = provider
    if model_name:
        session_updates["model_name"] = model_name
    
    if old_lang and new_lang and old_lang != new_lang:
        add_log(job_id, f"Mudança de idioma detectada: {old_lang} -> {new_lang}. Traduzindo workspace...")
        translation_session = session.copy()
        if provider:
            translation_session["provider"] = provider
        if model_name:
            translation_session["model_name"] = model_name
        translated = translate_workspace(translation_session, new_lang)
        if translated:
            session_updates.update({
                "artifacts": translated['artifacts'],
                "components": translated['components'],
            })
        update_session(job_id, session_updates)
    else:
        update_session(job_id, session_updates)
    
    if new_template_id and old_template_id != new_template_id:
        add_log(job_id, "Template de layout alterado. Regenerando relatório para ajustar componentes...")
        template_updates = {
            "settings": settings,
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {}
        }
        if provider:
            template_updates["provider"] = provider
        if model_name:
            template_updates["model_name"] = model_name
        update_session(job_id, template_updates)
        background_tasks.add_task(run_pipeline, job_id)
    else:
        add_log(job_id, "Configurações do relatório atualizadas. Re-renderizando...")
        background_tasks.add_task(run_script_generation, job_id)
    return {"status": "OK"}


@app.post("/api/sessions/{job_id}/regenerate")
async def regenerate_session(job_id: str, background_tasks: BackgroundTasks, payload: dict = Body(...)):
    session = require_session(job_id)
    scope = payload.get("scope")
    submitted_settings = payload.get("settings") or {}
    if submitted_settings:
        provider = submitted_settings.pop("provider", None)
        model_name = submitted_settings.pop("model_name", None)
        settings_updates = {"settings": submitted_settings}
        if provider:
            settings_updates["provider"] = provider
        if model_name:
            settings_updates["model_name"] = model_name
        session = update_session(job_id, settings_updates) or session

    artifacts = session.get("artifacts", [])
    components = session.get("components", {})

    if scope == "texts":
        text_ids = {a.get("id") for a in artifacts if a.get("type") == "text"}
        components = {key: value for key, value in components.items() if key not in text_ids}
        update_session(job_id, {"components": components})
        add_log(job_id, "Atualizando textos com o provider/modelo atual...")
        background_tasks.add_task(run_pipeline, job_id)
    elif scope == "graphics":
        graphic_ids = {a.get("id") for a in artifacts if a.get("type") in VISUAL_TYPES}
        components = {key: value for key, value in components.items() if key not in graphic_ids}
        update_session(job_id, {"components": components})
        add_log(job_id, "Atualizando gráficos com o provider/modelo atual...")
        background_tasks.add_task(run_pipeline, job_id)
    elif scope == "all":
        settings = session.get("settings", {}).copy()
        settings["title"] = ""
        update_session(job_id, {
            "settings": settings,
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
            "script": "",
        })
        add_log(job_id, "Atualizando relatório completo com o provider/modelo atual...")
        background_tasks.add_task(run_pipeline, job_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid regeneration scope")

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
