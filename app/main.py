from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Body, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List
import os
import logging
import sys
import uuid
import hmac
import base64
import hashlib
import mimetypes
import ast
import zipfile
from datetime import datetime, timezone
import time

# Permite rodar o script diretamente da raiz do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.layout import normalize_template
from app.utils.artifact_validation import validate_report_plan
from app.utils.component_library import (
    component_library_payload,
    component_preview_image_path,
    component_preview_matrix,
)
from app.utils.template_library import (
    delete_layout_template,
    load_layout_templates,
    upsert_layout_template,
)
from app.services.llm_service import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    assemble_script,
    dataframe_load_line,
    LLMBudgetError,
    LLMContractError,
    LLMTimeoutError,
    model_catalog,
    resolve_llm_config,
    refine_session_modular, 
    generate_exploration_script,
    translate_workspace
)
from app.services.executor_service import execute_script
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
    normalize_filter_prompt,
    run_pipeline,
    run_script_generation,
    should_consider_data_filter,
)
from app.services.session_manager import (
    create_session,
    get_session,
    get_session_lock_path,
    get_session_path,
    update_session,
    add_log,
    list_recent_sessions,
    set_session_stage,
)
from app.services.cleanup_service import (
    delete_entire_session,
    cleanup_expired_artifacts,
    get_workspace_usage,
    safe_remove_path
)
from app.services.web_helpers import (
    ALLOWED_UPLOAD_EXTENSIONS,
    MAX_EXCEL_SHEETS,
    MAX_UPLOAD_BYTES,
    UPLOAD_DIR,
    append_refinement_message,
    append_refinement_result,
    apply_global_visual_refinement,
    base_goal_for_refinement,
    build_refinement_goal,
    chart_palette_from_prompt,
    component_preview_filename,
    component_preview_script_from_session_script,
    component_preview_slot,
    copy_upload_with_limit,
    create_session,
    existing_refinement_messages,
    file_response,
    get_session_file,
    is_global_visual_refinement,
    process_chat_refinement,
    refinement_message,
    require_session,
    save_uploaded_files,
    session_file_variant,
    validate_component_code,
    validate_provider_model,
    validate_saved_upload,
    validate_upload,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Report Generator v0.3.6")

APP_ACCESS_SECRET = os.getenv("APP_ACCESS_SECRET") or os.getenv("PDF_REPORT_ACCESS_SECRET") or "change-me"
APP_ENV = os.getenv("APP_ENV", "development").lower()
ALLOW_DEFAULT_SECRET = APP_ENV in {"development", "dev", "local", "test"} or os.getenv("PDF_REPORT_ALLOW_DEFAULT_SECRET") == "1"
MAX_UPLOAD_BYTES = int(os.getenv("PDF_REPORT_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_EXCEL_SHEETS = int(os.getenv("PDF_REPORT_MAX_EXCEL_SHEETS", "20"))
AUTH_COOKIE_NAME = "pdf_report_session"
AUTH_SESSION_TTL_SECONDS = int(os.getenv("PDF_REPORT_SESSION_TTL_SECONDS", str(8 * 60 * 60)))
AUTH_COOKIE_SECURE = os.getenv("PDF_REPORT_COOKIE_SECURE", "1" if APP_ENV not in {"development", "dev", "local", "test"} else "0") == "1"
ALLOWED_UPLOAD_EXTENSIONS = {
    ".csv",
    ".xls",
    ".xlsx",
    ".pdf",
}

if APP_ACCESS_SECRET == "change-me" and not ALLOW_DEFAULT_SECRET:
    raise RuntimeError("APP_ACCESS_SECRET or PDF_REPORT_ACCESS_SECRET must be configured outside development mode.")

UPLOAD_DIR = "workspace/uploads"
for d in [UPLOAD_DIR, FILTERED_DIR, SCRIPT_DIR, REPORT_DIR]:
    os.makedirs(d, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _preserve_builtin_routes(routes):
    keep_paths = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    preserved = []
    for route in routes:
        path = getattr(route, "path", "")
        if path in keep_paths or path.startswith("/static"):
            preserved.append(route)
    return preserved


def is_valid_access_token(token: str) -> bool:
    return bool(token) and hmac.compare_digest(token, APP_ACCESS_SECRET)


def _sign_session_payload(payload: str) -> str:
    return hmac.new(APP_ACCESS_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token() -> str:
    issued_at = str(int(time.time()))
    nonce = uuid.uuid4().hex
    payload = f"{issued_at}.{nonce}"
    signature = _sign_session_payload(payload)
    raw_token = f"{payload}.{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw_token).decode("ascii")


def is_valid_session_token(token: str) -> bool:
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        issued_at, nonce, signature = decoded.split(".", 2)
        if not issued_at.isdigit() or not nonce:
            return False
        if int(time.time()) - int(issued_at) > AUTH_SESSION_TTL_SECONDS:
            return False
        expected = _sign_session_payload(f"{issued_at}.{nonce}")
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


def request_has_valid_auth(request: Request) -> bool:
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "")
    header_token = request.headers.get("x-access-token", "")
    return is_valid_session_token(cookie_token) or is_valid_session_token(header_token)


@app.middleware("http")
async def require_api_access(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/auth/login":
        if not request_has_valid_auth(request):
            response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
            add_security_headers(response)
            return response
    response = await call_next(request)
    add_security_headers(response)
    return response


def add_security_headers(response: Response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "form-action 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' blob: data:; "
        "frame-src 'self' blob:; "
        "connect-src 'self'",
    )


@app.post("/api/auth/login")
async def login(response: Response, payload: dict = Body(...)):
    if not is_valid_access_token(payload.get("secret", "")):
        raise HTTPException(status_code=401, detail="Invalid access secret")
    token = create_session_token()
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    return {"status": "OK", "expires_in": AUTH_SESSION_TTL_SECONDS}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME, httponly=True, secure=AUTH_COOKIE_SECURE, samesite="lax")
    return {"status": "OK"}


@app.get("/api/auth/check")
async def check_login():
    return {"status": "OK"}


@app.get("/api/models")
async def available_models():
    return model_catalog()


@app.get("/api/component-library")
async def visual_component_library():
    return component_library_payload()


@app.get("/api/component-library/previews")
async def visual_component_library_previews(theme: str | None = None, style: str | None = None):
    return {"previews": component_preview_matrix(theme or "executive_clean", style or "modern")}


@app.get("/api/component-library/presets/{preset_id}/preview")
async def visual_component_preset_preview(preset_id: str, theme: str | None = None, style: str | None = None):
    try:
        path = component_preview_image_path(preset_id, theme, style)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

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


def validate_provider_model(provider: str | None, model_name: str | None) -> tuple[str, str]:
    try:
        resolved_provider, resolved_model = resolve_llm_config(provider, model_name)
        return resolved_provider, model_name or resolved_model
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def refinement_message(role: str, content: str, kind: str = "message") -> dict:
    return {
        "role": role,
        "content": str(content or "").strip(),
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def existing_refinement_messages(session: dict) -> list:
    messages = [
        item for item in session.get("refinement_messages", [])
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]
    if messages:
        return messages

    return [
        {"role": "user", "content": str(item).strip(), "kind": "legacy"}
        for item in session.get("refinement_history", [])
        if str(item).strip()
    ]


def append_refinement_message(job_id: str, role: str, content: str, kind: str = "message"):
    session = get_session(job_id)
    if not session:
        return
    messages = existing_refinement_messages(session)
    messages.append(refinement_message(role, content, kind))
    update_session(job_id, {"refinement_messages": messages})


def base_goal_for_refinement(session: dict) -> str:
    if session.get("is_exploratory"):
        return DEFAULT_EXPLORATORY_PROMPT
    return session.get("initial_prompt") or session.get("current_goal") or DEFAULT_EXPLORATORY_PROMPT


def build_refinement_goal(session: dict, history: list[str]) -> str:
    base_goal = base_goal_for_refinement(session)
    clean_history = [str(item).strip() for item in history if str(item).strip()]
    if not clean_history:
        return base_goal
    return f"{base_goal}\nRefinement History:\n- " + "\n- ".join(clean_history)


def is_global_visual_refinement(prompt: str) -> bool:
    normalized = normalize_filter_prompt(prompt)
    style_terms = [
        "cor", "cores", "color", "colors", "paleta", "palette", "scheme",
        "tema", "theme", "visual", "estilo", "style", "tons", "tom",
        "vermelho", "vermelha", "red", "azul", "blue", "verde", "green",
    ]
    global_visual_terms = [
        "graficos", "charts", "todos os graficos", "todos graficos",
        "dos graficos", "as visualizacoes", "visualizacoes",
        "componentes graficos", "todos os charts", "all charts",
    ]
    return (
        any(term in normalized for term in style_terms)
        and any(term in normalized for term in global_visual_terms)
    )


def chart_palette_from_prompt(prompt: str) -> list[str] | None:
    normalized = normalize_filter_prompt(prompt)
    if "vermelh" in normalized or "red" in normalized:
        return ["#991b1b", "#b91c1c", "#dc2626", "#ef4444", "#f87171", "#fca5a5"]
    if "azul" in normalized or "blue" in normalized:
        return ["#1e3a8a", "#1d4ed8", "#2563eb", "#3b82f6", "#60a5fa", "#93c5fd"]
    if "verde" in normalized or "green" in normalized:
        return ["#14532d", "#166534", "#16a34a", "#22c55e", "#86efac", "#bbf7d0"]
    return None


def apply_global_visual_refinement(job_id: str, session: dict, prompt: str):
    settings = dict(session.get("settings") or DEFAULT_SETTINGS.copy())
    existing_instructions = settings.get("visual_instructions", "")
    instruction_lines = [
        line for line in str(existing_instructions).splitlines()
        if line.strip()
    ]
    new_instruction = f"- {prompt.strip()}"
    if new_instruction not in instruction_lines:
        instruction_lines.append(new_instruction)
    settings["visual_instructions"] = "\n".join(instruction_lines)

    palette = chart_palette_from_prompt(prompt)
    if palette:
        settings["chart_palette"] = palette

    visual_ids = {
        artifact.get("id")
        for artifact in session.get("artifacts", [])
        if artifact.get("type") in VISUAL_TYPES and artifact.get("id")
    }
    components = dict(session.get("components") or {})
    if visual_ids:
        components = {
            component_id: code
            for component_id, code in components.items()
            if component_id not in visual_ids
        }

    update_session(job_id, {
        "settings": settings,
        "components": components,
        "current_step": "refinement_visual",
        "progress": 20,
        "error": {},
    })
    add_log(job_id, f"Refinamento visual global detectado. Regenerando {len(visual_ids)} gráfico(s)...")
    append_refinement_message(job_id, "assistant", "Ajuste visual aplicado às instruções dos gráficos. Regenerando os componentes afetados.", "status")
    run_pipeline(job_id)


def append_refinement_result(job_id: str, success_message: str):
    session = get_session(job_id) or {}
    if session.get("current_step") == "failed":
        append_refinement_message(job_id, "assistant", "O refinamento foi processado, mas a renderização terminou com falha. Veja os logs para o erro exato.", "error")
        return
    append_refinement_message(job_id, "assistant", success_message, "status")


def process_chat_refinement(job_id: str, prompt: str):
    try:
        session = get_session(job_id)
        if not session:
            return

        set_session_stage(job_id, "refinement", 15, error={})

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
                "current_step": "refinement_preprocessing",
                "progress": 20,
            })
            append_refinement_message(job_id, "assistant", "Pedido entendido como ajuste de dados. Vou reavaliar o pré-processamento e regenerar o relatório.", "status")
            run_pipeline(job_id)
            append_refinement_result(job_id, "Refinamento concluído com pré-processamento atualizado.")
            return

        if is_global_visual_refinement(prompt):
            apply_global_visual_refinement(job_id, session, prompt)
            append_refinement_result(job_id, "Refinamento visual concluído.")
            return

        add_log(job_id, f"Processando refinamento modular: {prompt}")
        new_session_state = refine_session_modular(session, prompt, job_id=job_id)

        if new_session_state.get("needs_full_regeneration"):
            add_log(job_id, "Pedido global detectado. Reiniciando pipeline com novos requisitos...")
            update_session(job_id, {
                "artifacts": [],
                "template": [],
                "components": {},
                "calculated_metrics": {},
                "current_step": "refinement_full",
                "progress": 20,
            })
            append_refinement_message(job_id, "assistant", "Pedido entendido como alteração global. Vou regenerar o relatório com o novo objetivo.", "status")
            run_pipeline(job_id)
            append_refinement_result(job_id, "Refinamento global concluído.")
            return

        update_session(job_id, new_session_state)
        if new_session_state.get("needs_script_update"):
            append_refinement_message(job_id, "assistant", "Ajuste modular aplicado. Renderizando uma nova versão do PDF.", "status")
            run_script_generation(job_id)
            append_refinement_result(job_id, "Refinamento modular concluído.")
        else:
            add_log(job_id, "Refinamento analisado, mas nenhuma alteração aplicável foi detectada.")
            append_refinement_message(job_id, "assistant", "Analisei o pedido, mas não identifiquei uma alteração aplicável no relatório atual.", "status")

    except (LLMBudgetError, LLMTimeoutError) as exc:
        logger.exception("LLM limit error during refinement job_id=%s", job_id)
        error = exc.as_error_dict()
        error["stage"] = error.get("stage") or "refinement"
        set_session_stage(job_id, "failed", 100, error)
        add_log(job_id, f"Falha de limite LLM no refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", f"Não consegui aplicar o refinamento: {str(exc)}", "error")
    except LLMContractError as exc:
        logger.exception("LLM contract error during refinement job_id=%s", job_id)
        error = exc.as_error_dict()
        error["stage"] = error.get("stage") or "refinement"
        set_session_stage(job_id, "failed", 100, error)
        add_log(job_id, f"Falha de contrato LLM no refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", f"Não consegui aplicar o refinamento: {str(exc)}", "error")
    except Exception as exc:
        logger.exception("Refinement error job_id=%s", job_id)
        set_session_stage(job_id, "failed", 100, {
            "code": "refinement_error",
            "message": f"Falha no refinamento: {str(exc)}",
            "stage": "refinement",
            "retryable": True,
        })
        add_log(job_id, f"Falha no refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", f"Não consegui aplicar o refinamento: {str(exc)}", "error")


def validate_upload(file: UploadFile) -> str:
    filename = file.filename or ""
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_ext or 'unknown'}")
    return file_ext


def validate_saved_upload(path: str, file_ext: str, file_name: str | None = None, content_type: str | None = None) -> dict:
    diagnostics = {
        "status": "ok",
        "file_ext": file_ext,
        "file_name": file_name or os.path.basename(path),
        "content_type": content_type or mimetypes.guess_type(file_name or path)[0],
    }
    try:
        if file_ext == ".pdf":
            with open(path, "rb") as f:
                if f.read(5) != b"%PDF-":
                    raise HTTPException(status_code=400, detail="Invalid PDF file content")
            diagnostics["format"] = "pdf"
        elif file_ext in {".xls", ".xlsx"}:
            import pandas as pd

            if file_ext == ".xlsx" and not zipfile.is_zipfile(path):
                raise HTTPException(status_code=400, detail="Invalid Excel file content")
            if os.path.getsize(path) == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            workbook = pd.ExcelFile(path)
            sheet_names = list(workbook.sheet_names or [])
            if not sheet_names:
                raise HTTPException(status_code=400, detail="Excel workbook does not contain any readable sheets")
            if len(sheet_names) > MAX_EXCEL_SHEETS:
                raise HTTPException(status_code=400, detail=f"Excel workbook has too many sheets ({len(sheet_names)} > {MAX_EXCEL_SHEETS})")
            diagnostics.update({
                "format": "excel",
                "sheet_count": len(sheet_names),
                "sheet_names": sheet_names[:MAX_EXCEL_SHEETS],
            })
        elif file_ext == ".csv":
            with open(path, "rb") as f:
                sample = f.read(4096)
            if not sample:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            if b"\x00" in sample:
                raise HTTPException(status_code=400, detail="Invalid CSV file content")
            try:
                sample.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    sample.decode("latin-1")
                except UnicodeDecodeError as exc:
                    raise HTTPException(status_code=400, detail="CSV file encoding is not supported") from exc
            diagnostics["format"] = "csv"
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not validate uploaded file: {exc}") from exc
    return diagnostics


def copy_upload_with_limit(file: UploadFile, destination: str) -> int:
    total = 0
    with open(destination, "wb") as buffer:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                buffer.close()
                try:
                    os.remove(destination)
                except FileNotFoundError:
                    pass
                raise HTTPException(status_code=413, detail="Uploaded file exceeds the configured size limit")
            buffer.write(chunk)
    return total


def save_uploaded_files(files: List[UploadFile]) -> list:
    saved_files = []
    for file in files:
        file_ext = validate_upload(file)
        data_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{file_ext}")
        try:
            size_bytes = copy_upload_with_limit(file, data_path)
            validation = validate_saved_upload(data_path, file_ext, file.filename, file.content_type)
        except HTTPException:
            try:
                os.remove(data_path)
            except FileNotFoundError:
                pass
            raise
        saved_files.append({
            "original_filename": file.filename,
            "path": data_path,
            "size_bytes": size_bytes,
            "mime_type": file.content_type or mimetypes.guess_type(file.filename)[0],
            "validation": validation,
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


def validate_component_code(component_id: str, code: str):
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Python syntax for {component_id}: {exc.msg}")

    expected_name = f"render_{component_id}"
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    target = next((node for node in functions if node.name == expected_name), None)
    if target is None:
        raise HTTPException(status_code=400, detail=f"Custom code must define function {expected_name}")

    arg_names = [arg.arg for arg in target.args.args]
    required = ["pdf", "dataframes", "x", "y", "w", "h"]
    if arg_names[:len(required)] != required:
        raise HTTPException(status_code=400, detail=f"{expected_name} must start with arguments: {', '.join(required)}")


def component_preview_filename(job_id: str, component_id: str, suffix: str) -> str:
    safe_component_id = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_"
        for ch in str(component_id)
    )[:80] or "component"
    return f"{job_id}_{safe_component_id}_preview.{suffix}"


def component_preview_slot(session: dict, component_id: str) -> dict:
    slot = next(
        (item for item in normalize_template(session.get("template", [])) if item.get("id") == component_id),
        None,
    )
    if not slot:
        slot = {"w": 120, "h": 80}

    width = max(40, min(190, float(slot.get("w") or 120)))
    height = max(30, min(260, float(slot.get("h") or 80)))
    return {
        "id": component_id,
        "page": 1,
        "x": 10,
        "y": 10,
        "w": width,
        "h": height,
    }


def component_preview_script_from_session_script(
    session: dict,
    component_id: str,
    slot: dict,
    output_pdf_path: str,
) -> str | None:
    script = session.get("script") or ""
    if not script:
        return None

    try:
        tree = ast.parse(script)
    except SyntaxError:
        return None

    target_name = f"render_{component_id}"
    generate_node = None
    has_target = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "generate_report":
            generate_node = node
        if isinstance(node, ast.FunctionDef) and node.name == target_name:
            has_target = True

    if not generate_node or not has_target:
        return None

    prefix = "\n".join(script.splitlines()[:generate_node.lineno - 1]).rstrip()
    files_map = build_files_map(session)
    page_width = slot["w"] + 20
    page_height = slot["h"] + 20

    preview_lines = [
        "",
        "def generate_report():",
        "    dataframes = {}",
    ]
    for original_name, path in files_map.items():
        line = dataframe_load_line(original_name, path, indent="    ")
        if line:
            preview_lines.append(line)

    preview_lines.extend([
        f"    pdf = FPDF(format=({float(page_width)}, {float(page_height)}))",
        "    pdf.set_auto_page_break(auto=False)",
        "    pdf.add_page()",
        "    component_manifest = []",
        "    try:",
        "        primary_df = list(dataframes.values())[0] if dataframes else None",
        "        try:",
        f"            {target_name}(pdf, dataframes, {slot['x']}, {slot['y']}, {slot['w']}, {slot['h']}, df=primary_df)",
        "        except TypeError:",
        f"            {target_name}(pdf, dataframes, {slot['x']}, {slot['y']}, {slot['w']}, {slot['h']})",
        f"        component_manifest.append({{'id': {component_id!r}, 'status': 'rendered'}})",
        "    except Exception as e:",
        f"        component_manifest.append({{'id': {component_id!r}, 'status': 'failed', 'error': str(e)}})",
        "        import logging",
        f"        logging.getLogger(__name__).exception('Erro no componente {component_id}')",
        f"    manifest_path = r'{output_pdf_path}.manifest.json'",
        "    with open(manifest_path, 'w', encoding='utf-8') as manifest_file:",
        "        json.dump({'components': component_manifest}, manifest_file, indent=2, ensure_ascii=False)",
        "    failed_components = [item for item in component_manifest if item.get('status') != 'rendered']",
        "    if failed_components:",
        "        raise RuntimeError('Report component rendering failed: ' + ', '.join(item['id'] for item in failed_components))",
        f"    pdf.output(r'{output_pdf_path}')",
        "",
        "if __name__ == '__main__':",
        "    generate_report()",
    ])
    return prefix + "\n" + "\n".join(preview_lines)


@app.post("/api/sessions")
async def start_session(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    prompt: str = Form(""),
    provider: str = Form(DEFAULT_PROVIDER),
    model_name: str = Form(DEFAULT_MODEL_NAME)
):
    provider, model_name = validate_provider_model(provider, model_name)
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
        background_tasks.add_task(run_pipeline, job_id)
    return {"status": "OK"}

@app.get("/api/sessions/{job_id}")
async def get_session_status(job_id: str):
    return require_session(job_id)


@app.delete("/api/sessions/{job_id}")
async def delete_session(job_id: str):
    # delete_entire_session handles get_session internally and removes everything
    removed_count = delete_entire_session(job_id)
    return {"status": "OK", "removed_files": removed_count}


@app.get("/api/admin/workspace-usage")
async def workspace_usage():
    return get_workspace_usage()


@app.post("/api/admin/cleanup")
async def admin_cleanup(background_tasks: BackgroundTasks, policy: dict = Body(None)):
    background_tasks.add_task(cleanup_expired_artifacts, policy)
    return {"status": "Cleaning started in background"}


@app.post("/api/sessions/{job_id}/chat")
async def chat_refinement(job_id: str, background_tasks: BackgroundTasks, prompt: str = Form(...)):
    session = require_session(job_id)
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise HTTPException(status_code=400, detail="Feedback cannot be empty")

    history = [str(item).strip() for item in session.get("refinement_history", []) if str(item).strip()]
    history.append(clean_prompt)
    messages = existing_refinement_messages(session)
    messages.append(refinement_message("user", clean_prompt))
    messages.append(refinement_message("assistant", "Feedback recebido. Iniciando o refinamento.", "status"))
    full_current_goal = build_refinement_goal(session, history)

    update_session(job_id, {
        "refinement_history": history,
        "refinement_messages": messages,
        "current_goal": full_current_goal,
        "current_step": "refinement_queued",
        "progress": 5,
        "error": {},
    })
    add_log(job_id, f"Feedback recebido para refinamento: {clean_prompt}")
    background_tasks.add_task(process_chat_refinement, job_id, clean_prompt)
    return {"status": "queued"}

@app.put("/api/sessions/{job_id}/artifacts")
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

    if old_lang and new_lang and old_lang != new_lang:
        add_log(job_id, f"Mudança de idioma detectada: {old_lang} -> {new_lang}. Traduzindo workspace...")
        translation_session = session.copy()
        translation_session["settings"] = merged_settings
        if provider:
            translation_session["provider"] = provider
        if model_name:
            translation_session["model_name"] = model_name
        try:
            translated = translate_workspace(translation_session, new_lang, job_id=job_id)
            if translated:
                session_updates.update({
                    "artifacts": translated['artifacts'],
                    "components": translated['components'],
                })
        except (LLMBudgetError, LLMTimeoutError) as exc:
            update_session(job_id, {
                **session_updates,
                "error": exc.as_error_dict(),
                "status": "Falha na tradução do workspace",
                "current_step": "translation_failed",
                "progress": 100,
            })
            add_log(job_id, f"Falha de limite LLM na tradução do workspace: {str(exc)}")
            raise HTTPException(status_code=429 if isinstance(exc, LLMBudgetError) else 504, detail=str(exc)) from exc
        except LLMContractError as exc:
            update_session(job_id, {
                **session_updates,
                "error": exc.as_error_dict(),
                "status": "Falha na tradução do workspace",
                "current_step": "translation_failed",
                "progress": 100,
            })
            add_log(job_id, f"Falha de contrato LLM na tradução do workspace: {str(exc)}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        update_session(job_id, session_updates)
    else:
        update_session(job_id, session_updates)
    
    if new_template_id and old_template_id != new_template_id:
        add_log(job_id, "Template de layout alterado. Regenerando relatório para ajustar componentes...")
        template_updates = {
            "settings": merged_settings,
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
    elif old_chart_style_id != new_chart_style_id or old_visual_instructions != new_visual_instructions:
        add_log(job_id, "Estilo visual alterado. Regenerando componentes gráficos...")
        visual_ids = {
            artifact.get("id")
            for artifact in session.get("artifacts", [])
            if artifact.get("type") in VISUAL_TYPES and artifact.get("id")
        }
        components = {
            component_id: code
            for component_id, code in session.get("components", {}).items()
            if component_id not in visual_ids
        }
        update_session(job_id, {"settings": merged_settings, "components": components})
        background_tasks.add_task(run_pipeline, job_id)
    else:
        add_log(job_id, "Configurações do relatório atualizadas. Re-renderizando...")
        background_tasks.add_task(run_script_generation, job_id)
    return {"status": "OK"}


@app.post("/api/sessions/{job_id}/regenerate")
async def regenerate_session(job_id: str, background_tasks: BackgroundTasks, payload: dict = Body(...)):
    session = require_session(job_id)
    scope = payload.get("scope")
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
        background_tasks.add_task(run_pipeline, job_id)
    elif scope == "graphics":
        graphic_ids = {a.get("id") for a in artifacts if a.get("type") in VISUAL_TYPES}
        components = {key: value for key, value in components.items() if key not in graphic_ids}
        update_session(job_id, {"components": components})
        add_log(job_id, "Atualizando gráficos com o provider/modelo atual...")
        background_tasks.add_task(run_pipeline, job_id)
    elif scope == "component":
        component_id = payload.get("component_id")
        artifact = next((a for a in artifacts if a.get("id") == component_id), None)
        if not artifact:
            raise HTTPException(status_code=404, detail="Component not found")
        components = {key: value for key, value in components.items() if key != component_id}
        update_session(job_id, {"components": components})
        add_log(job_id, f"Atualizando componente {component_id} com o provider/modelo atual...")
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


@app.get("/api/sessions/{job_id}/components/{component_id}/preview")
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
    output_pdf_path = os.path.abspath(os.path.join(REPORT_DIR, component_preview_filename(job_id, component_id, "pdf")))
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
    result = execute_script(script_content, script_path, timeout=30)
    if not result.get("success"):
        detail = (result.get("stderr") or result.get("stdout") or "Component preview failed").strip()
        raise HTTPException(status_code=500, detail=detail[-1000:])
    if not os.path.exists(output_pdf_path):
        raise HTTPException(status_code=500, detail="Component preview file was not created")
    return FileResponse(output_pdf_path, media_type="application/pdf", headers={"Cache-Control": "no-store"})

@app.post("/api/sessions/{job_id}/explore")
async def explore_data(job_id: str, prompt: str = Form(...)):
    session = require_session(job_id)
    
    history = session.get('explore_history', [])
    all_metadata = metadata_json(session)
    files_map = build_files_map(session)
    try:
        script_content = generate_exploration_script(
            all_metadata, prompt, files_map, session.get('provider'), session.get('model_name'), history=history, job_id=job_id
        )
        script_path = os.path.join(SCRIPT_DIR, f"explore_{job_id}.py")
        result = execute_script(script_content, script_path)
        answer = result["stdout"] if result["success"] else f"Erro: {result['stderr']}"
    except (LLMBudgetError, LLMTimeoutError) as exc:
        answer = f"Erro: {str(exc)}"
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": answer})
        update_session(job_id, {"explore_history": history, "error": exc.as_error_dict(), "status": "Falha na exploração", "current_step": "exploration_failed", "progress": 100})
        raise HTTPException(status_code=429 if isinstance(exc, LLMBudgetError) else 504, detail=str(exc)) from exc

    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": answer})
    update_session(job_id, {"explore_history": history})
    return {"answer": answer}

if __name__ == "app.main":
    from app.routers.api import router as api_router

    app.router.routes = _preserve_builtin_routes(app.router.routes)
    app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["app", "static"])
