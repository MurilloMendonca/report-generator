"""HTTP routes for the report workspace."""
import os
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from app.security import require_admin, require_verified_user
from app.services.cleanup_service import (
    cleanup_expired_artifacts,
    delete_entire_session,
    get_workspace_usage,
    safe_remove_path,
)
from app.services.executor_service import execute_script
from app.services.exploration_service import ExplorationError, complete_exploration_job, start_exploration
from app.services.job_runner import run_job
from app.services.llm.client import model_catalog
from app.services.llm.script import assemble_script
from app.utils.model_config import DEFAULT_PROVIDER
from app.services.pipeline_service import (
    DEFAULT_EXPLORATORY_PROMPT,
    DEFAULT_SETTINGS,
    VISUAL_TYPES,
    build_files_map,
    run_pipeline,
    run_script_generation,
)
from app.services.session_manager import (
    add_log,
    create_session,
    get_session,
    is_valid_job_id,
    list_recent_sessions,
    update_session,
)
from app.services.web_helpers import (
    component_preview_filename,
    component_preview_script_from_session_script,
    component_preview_slot,
    existing_refinement_messages,
    file_response,
    get_session_file,
    process_chat_refinement,
    refinement_message,
    require_session,
    translate_then_continue,
    save_uploaded_files,
    validate_component_code,
    validate_provider_model,
)
from app.utils.artifact_validation import validate_report_plan
from app.utils.component_library import (
    component_library_payload,
    component_preview_image_path,
    component_preview_matrix,
)
from app.utils.exploration_charts import chart_path, validate_chart_image
from app.utils.layout import normalize_template
from app.utils.report_paths import ReportPaths
from app.utils.template_library import (
    delete_layout_template,
    load_layout_templates,
    upsert_layout_template,
)

router = APIRouter()


@router.get("/api/models")
async def available_models():
    return model_catalog()


@router.get("/api/component-library")
async def visual_component_library():
    return component_library_payload()


@router.get("/api/component-library/previews")
async def visual_component_library_previews(theme: str | None = None, style: str | None = None):
    return {"previews": component_preview_matrix(theme or "executive_clean", style or "modern")}


@router.get("/api/component-library/presets/{preset_id}/preview")
async def visual_component_preset_preview(preset_id: str, theme: str | None = None, style: str | None = None):
    try:
        path = await run_in_threadpool(component_preview_image_path, preset_id, theme, style)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/", response_class=HTMLResponse)
async def read_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@router.get("/templates", response_class=HTMLResponse)
async def read_template_editor():
    with open("static/templates.html", "r", encoding="utf-8") as f:
        return f.read()


@router.get("/session/{job_id}", response_class=HTMLResponse)
async def read_session_workspace(job_id: str):
    require_session(job_id)
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@router.post("/api/sessions")
async def start_session(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    prompt: str = Form(""),
    provider: str = Form(DEFAULT_PROVIDER),
    model_name: str | None = Form(None)
):
    user = require_verified_user(request)
    provider, model_name = validate_provider_model(provider, model_name)
    job_id = str(uuid.uuid4())
    # Files go under the owner's folder; Phase 3 restricts access to the owner.
    report_paths = ReportPaths.for_new_report(job_id, str(user.id)).ensure()
    session_files = await run_in_threadpool(save_uploaded_files, files, report_paths.uploads)
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
        "refinement_messages": [],
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
        "document_insights": {},
        "settings": DEFAULT_SETTINGS.copy(),
        "script": "",
        "provider": provider,
        "model_name": model_name,
        "data_filter": {},
        "data_preprocessing": {},
        "pdf_version": 0,
        "retry_count": 0,
        "explore_history": [],
        **report_paths.session_fields(),
    }, owner_id=user.id)

    background_tasks.add_task(run_job, run_pipeline, job_id)
    return {"job_id": job_id}


@router.get("/api/sessions")
async def recent_sessions():
    return {"sessions": list_recent_sessions()}


@router.post("/api/sessions/{job_id}/files")
async def add_files_to_session(
    job_id: str,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...)
):
    session = require_session(job_id)
    
    session_files = session.get("files", [])
    existing_names = {file["original_filename"].casefold() for file in session_files}
    if any(str(file.filename or "").casefold() in existing_names for file in files):
        raise HTTPException(status_code=400, detail="A file with this name already exists in the session")
    session_files.extend(await run_in_threadpool(save_uploaded_files, files, ReportPaths.from_session(session).uploads))
    
    update_session(job_id, {"files": session_files})
    add_log(job_id, f"Adicionados {len(files)} novos arquivos.")
    background_tasks.add_task(run_job, run_pipeline, job_id)
    return {"status": "OK"}


@router.get("/api/sessions/{job_id}/files/{file_index}/download")
async def download_session_file(job_id: str, file_index: int, variant: str = "original"):
    session = require_session(job_id)
    return file_response(get_session_file(session, file_index), variant=variant)


@router.get("/api/sessions/{job_id}/files/{file_index}/preview")
async def preview_session_file(job_id: str, file_index: int, variant: str = "original"):
    session = require_session(job_id)
    return file_response(get_session_file(session, file_index), inline=True, variant=variant)


@router.delete("/api/sessions/{job_id}/files/{file_index}")
async def delete_session_file(job_id: str, file_index: int, background_tasks: BackgroundTasks):
    session = require_session(job_id)
    file_info = get_session_file(session, file_index)
    files = session.get("files", [])
    removed = files.pop(file_index)

    safe_remove_path(file_info["path"])
    for generated_path in [
        file_info.get("preprocessed_path"),
        file_info.get("filtered_path"),
    ]:
        if generated_path and generated_path != file_info.get("path"):
            safe_remove_path(generated_path)

    update_payload = {
        "files": files,
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
        "document_insights": {},
    }
    if not files:
        update_payload.update({"status": "Aguardando arquivos", "script": "", "pdf_version": session.get("pdf_version", 0)})

    update_session(job_id, update_payload)
    add_log(job_id, f"Arquivo removido da sessão: {removed.get('original_filename', 'arquivo')}")
    if files:
        background_tasks.add_task(run_job, run_pipeline, job_id)
    return {"status": "OK"}


@router.get("/api/sessions/{job_id}")
async def get_session_status(job_id: str):
    return require_session(job_id)


@router.delete("/api/sessions/{job_id}")
async def delete_session(job_id: str):
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=404)
    # delete_entire_session handles get_session internally and removes everything
    removed_count = delete_entire_session(job_id)
    return {"status": "OK", "removed_files": removed_count}


@router.get("/api/admin/workspace-usage")
async def workspace_usage(request: Request):
    require_admin(request)
    return get_workspace_usage()


@router.post("/api/admin/cleanup")
async def admin_cleanup(request: Request, background_tasks: BackgroundTasks, policy: dict = Body(None)):
    require_admin(request)
    background_tasks.add_task(cleanup_expired_artifacts, policy)
    return {"status": "Cleaning started in background"}


@router.post("/api/sessions/{job_id}/chat")
async def chat_refinement(job_id: str, background_tasks: BackgroundTasks, prompt: str = Form(...)):
    session = require_session(job_id)
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise HTTPException(status_code=400, detail="Feedback cannot be empty")

    messages = existing_refinement_messages(session)
    messages.append(refinement_message("user", clean_prompt))
    messages.append(refinement_message("assistant", "Feedback recebido. Iniciando o refinamento.", "status"))
    update_session(job_id, {
        "refinement_messages": messages,
        "current_step": "refinement_queued",
        "progress": 5,
        "error": {},
    })
    add_log(job_id, f"Feedback recebido para refinamento: {clean_prompt}")
    background_tasks.add_task(run_job, process_chat_refinement, job_id, clean_prompt)
    return {"status": "queued"}


@router.put("/api/sessions/{job_id}/artifacts")
async def update_artifacts_manually(job_id: str, background_tasks: BackgroundTasks, payload: dict = Body(...)):
    session = require_session(job_id)
    
    new_artifacts = payload.get("artifacts", [])
    new_components = payload.get("components", {})
    defer_pipeline = bool(payload.get("defer_pipeline"))
    old_artifacts = session.get('artifacts', [])
    current_components = session.get('components', {})
    metadata_items = [file_info.get("artifact") for file_info in session.get("files", []) if file_info.get("artifact")]
    new_artifacts, validation_issues = validate_report_plan(new_artifacts, metadata_items)
    # Detecção de mudanças para invalidar cache de código
    manual_override_ids = list(new_components.keys())

    for new_art in new_artifacts:
        # Garante que o tipo nunca fique vazio (fallback para text se sumir)
        if not new_art.get('type'):
            new_art['type'] = 'graphic' # Fallback seguro

        old_art = next((a for a in old_artifacts if a.get('id') == new_art.get('id')), None)
        if old_art:
            changed = (
                old_art.get('type') != new_art.get('type') or
                old_art.get('sub_type') != new_art.get('sub_type') or
                old_art.get('component_preset_id') != new_art.get('component_preset_id') or
                old_art.get('chart_style_id') != new_art.get('chart_style_id') or
                old_art.get('visual_options') != new_art.get('visual_options') or
                old_art.get('data_binding') != new_art.get('data_binding') or
                old_art.get('render_mode') != new_art.get('render_mode') or
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
    for component_id, code in new_components.items():
        validate_component_code(component_id, code)
    current_components.update(new_components)


    active_ids = [a['id'] for a in new_artifacts]
    current_components = {id: code for id, code in current_components.items() if id in active_ids}
    
    update_session(job_id, {
        "artifacts": new_artifacts, "components": current_components, "template": [], "artifact_validation": validation_issues
    })
    
    if not defer_pipeline:
        background_tasks.add_task(run_job, run_pipeline, job_id)
    return {"status": "OK"}


@router.put("/api/sessions/{job_id}/template")
async def update_template_manually(job_id: str, background_tasks: BackgroundTasks, template: list = Body(...)):
    session = require_session(job_id)
    
    update_session(job_id, {"template": normalize_template(template)})
    add_log(job_id, "Layout visual atualizado pelo usuário. Re-renderizando...")
    background_tasks.add_task(run_job, run_script_generation, job_id)
    return {"status": "OK"}


@router.put("/api/sessions/{job_id}/settings")
async def update_settings(job_id: str, background_tasks: BackgroundTasks, settings: dict = Body(...)):
    session = require_session(job_id)
    submitted_settings = dict(settings or {})
    provider = submitted_settings.pop("provider", None)
    model_name = submitted_settings.pop("model_name", None)
    if provider or model_name:
        provider, model_name = validate_provider_model(provider or session.get("provider"), model_name)
    
    current_settings = dict(session.get('settings') or DEFAULT_SETTINGS.copy())
    merged_settings = {**current_settings, **submitted_settings}
    old_lang = current_settings.get('language')
    new_lang = merged_settings.get('language')
    old_template_id = current_settings.get('selected_template_id')
    new_template_id = merged_settings.get('selected_template_id')
    old_chart_style_id = current_settings.get('chart_style_id')
    new_chart_style_id = merged_settings.get('chart_style_id')
    old_visual_instructions = current_settings.get('visual_instructions', '')
    new_visual_instructions = merged_settings.get('visual_instructions', '')
    session_updates = {"settings": merged_settings}
    if provider:
        session_updates["provider"] = provider
    if model_name:
        session_updates["model_name"] = model_name

    language_changed = bool(old_lang and new_lang and old_lang != new_lang)
    update_session(job_id, session_updates)

    follow_up = run_script_generation
    drop_component_ids: set = set()
    if new_template_id and old_template_id != new_template_id:
        add_log(job_id, "Template de layout alterado. Regenerando relatório para ajustar componentes...")
        update_session(job_id, {
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
        })
        follow_up = run_pipeline
        # The pipeline regenerates everything in the new language; nothing to translate.
        language_changed = False
    elif old_chart_style_id != new_chart_style_id or old_visual_instructions != new_visual_instructions:
        add_log(job_id, "Estilo visual alterado. Regenerando componentes gráficos...")
        drop_component_ids = {
            artifact.get("id")
            for artifact in session.get("artifacts", [])
            if artifact.get("type") in VISUAL_TYPES and artifact.get("id")
        }
        components = {
            component_id: code
            for component_id, code in session.get("components", {}).items()
            if component_id not in drop_component_ids
        }
        update_session(job_id, {"components": components})
        follow_up = run_pipeline
    else:
        add_log(job_id, "Configurações do relatório atualizadas. Re-renderizando...")

    if language_changed:
        add_log(job_id, f"Mudança de idioma detectada: {old_lang} -> {new_lang}. Traduzindo workspace...")
        update_session(job_id, {"current_step": "translating", "progress": 5, "error": {}})
        background_tasks.add_task(run_job, translate_then_continue, job_id, new_lang, follow_up, drop_component_ids)
        return {"status": "queued", "translating": True}
    background_tasks.add_task(run_job, follow_up, job_id)
    return {"status": "OK"}


@router.post("/api/sessions/{job_id}/regenerate")
async def regenerate_session(job_id: str, background_tasks: BackgroundTasks, payload: dict = Body(...)):
    session = require_session(job_id)
    scope = payload.get("scope")
    if scope not in {"texts", "graphics", "component", "all"}:
        raise HTTPException(status_code=400, detail="Invalid regeneration scope")
    if scope == "component" and not any(a.get("id") == payload.get("component_id") for a in session.get("artifacts", [])):
        raise HTTPException(status_code=404, detail="Component not found")
    submitted_settings = dict(payload.get("settings") or {})
    if submitted_settings:
        provider = submitted_settings.pop("provider", None)
        model_name = submitted_settings.pop("model_name", None)
        if provider or model_name:
            provider, model_name = validate_provider_model(provider or session.get("provider"), model_name)
        current_settings = dict(session.get("settings") or DEFAULT_SETTINGS.copy())
        settings_updates = {"settings": {**current_settings, **submitted_settings}}
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
        background_tasks.add_task(run_job, run_pipeline, job_id)
    elif scope == "graphics":
        graphic_ids = {a.get("id") for a in artifacts if a.get("type") in VISUAL_TYPES}
        components = {key: value for key, value in components.items() if key not in graphic_ids}
        update_session(job_id, {"components": components})
        add_log(job_id, "Atualizando gráficos com o provider/modelo atual...")
        background_tasks.add_task(run_job, run_pipeline, job_id)
    elif scope == "component":
        component_id = payload.get("component_id")
        artifact = next((a for a in artifacts if a.get("id") == component_id), None)
        if not artifact:
            raise HTTPException(status_code=404, detail="Component not found")
        components = {key: value for key, value in components.items() if key != component_id}
        update_session(job_id, {"components": components})
        add_log(job_id, f"Atualizando componente {component_id} com o provider/modelo atual...")
        background_tasks.add_task(run_job, run_pipeline, job_id)
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
        background_tasks.add_task(run_job, run_pipeline, job_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid regeneration scope")

    return {"status": "OK"}


@router.get("/api/layout-templates")
async def list_layout_templates():
    return {"templates": load_layout_templates()}


@router.post("/api/layout-templates")
async def create_layout_template(payload: dict = Body(...)):
    return upsert_layout_template(payload)


@router.put("/api/layout-templates/{template_id}")
async def update_layout_template(template_id: str, payload: dict = Body(...)):
    payload["id"] = template_id
    return upsert_layout_template(payload)


@router.delete("/api/layout-templates/{template_id}")
async def remove_layout_template(template_id: str):
    if not delete_layout_template(template_id):
        raise HTTPException(status_code=404)
    return {"status": "OK"}


@router.get("/api/sessions/{job_id}/pdf")
async def get_pdf(job_id: str):
    session = get_session(job_id)
    if not session or not os.path.exists(session.get('pdf_path', '')): raise HTTPException(status_code=404)
    return FileResponse(session['pdf_path'], media_type="application/pdf")


@router.get("/api/sessions/{job_id}/components/{component_id}/preview")
async def preview_component(job_id: str, component_id: str):
    session = require_session(job_id)
    component_code = session.get("components", {}).get(component_id)
    artifact = next((item for item in session.get("artifacts", []) if item.get("id") == component_id), None)
    if artifact:
        metadata_items = [file_info.get("artifact") for file_info in session.get("files", []) if file_info.get("artifact")]
        normalized_artifacts, _validation_issues = validate_report_plan([artifact], metadata_items)
        artifact = normalized_artifacts[0] if normalized_artifacts else artifact
    render_mode = str((artifact or {}).get("render_mode") or "generated_code")
    if not component_code and render_mode not in {"library", "hybrid"}:
        raise HTTPException(status_code=404, detail="Component code not found")

    slot = component_preview_slot(session, component_id)
    page_width = slot["w"] + 20
    page_height = slot["h"] + 20
    report_paths = ReportPaths.from_session(session).ensure()
    output_pdf_path = os.path.abspath(os.path.join(report_paths.output, component_preview_filename(job_id, component_id, "pdf")))
    script_path = os.path.join("/tmp", component_preview_filename(job_id, component_id, "py"))
    script_content = component_preview_script_from_session_script(session, component_id, slot, output_pdf_path) if component_code else None
    if not script_content:
        script_content = assemble_script(
            {component_id: component_code} if component_code else {},
            [slot],
            build_files_map(session),
            output_pdf_path,
            settings=session.get("settings") or DEFAULT_SETTINGS.copy(),
            page_format=(page_width, page_height),
            include_title=False,
            normalize_layout=False,
            artifacts=[artifact] if artifact else None,
        )
    result = await run_in_threadpool(execute_script, script_content, script_path, timeout=30, paths=report_paths)
    if not result.get("success"):
        detail = (result.get("stderr") or result.get("stdout") or "Component preview failed").strip()
        raise HTTPException(status_code=500, detail=detail[-1000:])
    if not os.path.exists(output_pdf_path):
        raise HTTPException(status_code=500, detail="Component preview file was not created")
    return FileResponse(output_pdf_path, media_type="application/pdf", headers={"Cache-Control": "no-store"})


@router.post("/api/sessions/{job_id}/explore", status_code=202)
async def explore_data(job_id: str, background_tasks: BackgroundTasks, prompt: str = Form(...)):
    """Record the question and answer it in the background; the UI polls the session.

    Answering can take longer than Cloudflare's ~100 s request limit, so it never
    happens inside the request.
    """
    require_session(job_id)
    try:
        started = await run_in_threadpool(start_exploration, job_id, prompt)
    except ExplorationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.details) from exc
    background_tasks.add_task(run_job, complete_exploration_job, job_id, started["turn_id"], prompt.strip())
    return started


@router.get("/api/sessions/{job_id}/explore/{turn_id}/charts/{chart_id}")
async def get_exploration_chart(job_id: str, turn_id: str, chart_id: str, download: bool = False):
    session = require_session(job_id)
    registered = any(
        message.get("role") == "assistant" and message.get("turn_id") == turn_id
        and any(chart.get("id") == chart_id for chart in message.get("charts", []) if isinstance(chart, dict))
        for message in session.get("explore_history", [])
    )
    if not registered:
        raise HTTPException(status_code=404, detail="Gráfico não encontrado nesta sessão.")
    try:
        path = chart_path(job_id, turn_id, chart_id, ReportPaths.from_session(session).output)
        await run_in_threadpool(validate_chart_image, path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail="Gráfico indisponível.") from exc
    return FileResponse(
        str(path), media_type="image/png", filename=f"grafico-{chart_id}.png",
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "no-store"},
    )
