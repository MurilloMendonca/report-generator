import ast
import mimetypes
import os
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services.pipeline_service import DEFAULT_EXPLORATORY_PROMPT
from app.services.llm.client import llm_operation_context, resolve_llm_config
from app.services.llm.errors import LLMBudgetError, LLMContractError, LLMTimeoutError
from app.services.llm.metrics import dataframe_load_line
from app.services.llm.refinement import classify_refinement, refine_session_modular, translate_workspace
from app.services.pipeline_service import (
    add_log,
    build_files_map as pipeline_build_files_map,
    clear_data_filter_state,
    run_pipeline,
    run_script_generation,
)
from app.services.session_manager import (
    get_session,
    preserve_report_on_failure,
    set_session_stage,
    update_session,
)
from app.utils.layout import normalize_template

UPLOAD_DIR = "workspace/uploads"
ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xls", ".xlsx", ".pdf"}
MAX_UPLOAD_BYTES = int(os.getenv("PDF_REPORT_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
# Total per request; Cloudflare's free plan rejects bodies over 100 MB before they reach the app.
MAX_REQUEST_UPLOAD_BYTES = int(os.getenv("PDF_REPORT_MAX_REQUEST_UPLOAD_BYTES", str(90 * 1024 * 1024)))
MAX_EXCEL_SHEETS = int(os.getenv("PDF_REPORT_MAX_EXCEL_SHEETS", "20"))


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


def append_refinement_result(job_id: str, success_message: str):
    session = get_session(job_id) or {}
    if session.get("current_step") == "failed":
        append_refinement_message(job_id, "assistant", "Não foi possível concluir o refinamento. A última versão do relatório foi preservada; veja os logs para o erro exato.", "error")
        return
    append_refinement_message(job_id, "assistant", success_message, "status")


@preserve_report_on_failure
def process_chat_refinement(job_id: str, prompt: str):
    try:
        session = get_session(job_id)
        if not session:
            return
        set_session_stage(job_id, "refinement_routing", 15, error={})
        with llm_operation_context(job_id=job_id, operation="refinement", settings=session.get("settings")):
            decision = classify_refinement(session, prompt, job_id=job_id)
            add_log(job_id, f"Refinamento classificado pelo modelo: {decision['intent']} ({decision['scope']}).")
            if decision["intent"] == "clarify":
                update_session(job_id, {"pending_refinement": {
                    "prompt": "\n".join(filter(None, [(session.get("pending_refinement") or {}).get("prompt"), prompt]))[-6000:],
                    "question": decision["clarification_question"],
                }})
                append_refinement_message(job_id, "assistant", decision["clarification_question"], "clarification")
                add_log(job_id, "Aguardando esclarecimento; o relatório não foi alterado.")
                set_session_stage(job_id, "awaiting_clarification", 100, error={})
                return

            state = refine_session_modular(session, prompt, job_id=job_id, decision=decision)
            history = [str(item).strip() for item in session.get("refinement_history", []) if str(item).strip()]
            history.append(decision["instruction"])
            changes = {
                key: state[key] for key in ("settings", "artifacts", "components", "template")
                if key in state
            }
            changes.update({
                "refinement_history": history,
                "current_goal": build_refinement_goal(session, history),
                "pending_refinement": {},
                "needs_script_update": False,
                "needs_full_regeneration": False,
                "needs_data_preprocessing": False,
                "error": {},
            })
            if state.get("needs_full_regeneration"):
                changes.update({
                    "artifacts": [], "template": [], "components": {}, "calculated_metrics": {},
                    "component_validation_retries": {}, "current_step": "refinement_full", "progress": 20,
                })
                if state.get("needs_data_preprocessing"):
                    changes.update({
                        "files": clear_data_filter_state(session.get("files", [])),
                        "data_filter": {}, "data_preprocessing": {}, "current_step": "refinement_preprocessing",
                    })
                update_session(job_id, changes)
                append_refinement_message(job_id, "assistant", "Pedido classificado e validado. Atualizando os dados e o relatório." if state.get("needs_data_preprocessing") else "Pedido de alteração do conteúdo validado. Atualizando o relatório.", "status")
                run_pipeline(job_id)
            else:
                changes.update(current_step="refinement_render", progress=70)
                update_session(job_id, changes)
                append_refinement_message(job_id, "assistant", "Ajuste aplicado ao escopo solicitado. Renderizando uma nova versão do PDF.", "status")
                run_script_generation(job_id)
            append_refinement_result(job_id, "Refinamento concluído.")

    except (LLMBudgetError, LLMTimeoutError, LLMContractError) as exc:
        error = exc.as_error_dict()
        error["stage"] = error.get("stage") or "refinement_routing"
        set_session_stage(job_id, "failed", 100, error)
        add_log(job_id, f"Não foi possível aplicar o refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", "Não consegui interpretar ou concluir esse pedido com segurança. O relatório foi preservado. Tente novamente ou esclareça a alteração desejada.", "error")
    except Exception as exc:
        set_session_stage(job_id, "failed", 100, {
            "code": "refinement_error", "message": str(exc), "stage": "refinement", "retryable": True,
        })
        add_log(job_id, f"Falha no refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", "Não consegui aplicar o refinamento. O relatório foi preservado; veja os logs para o erro exato.", "error")


def translate_then_continue(job_id: str, target_lang: str, follow_up, drop_component_ids=()):
    """Background job: translate the workspace, then run the follow-up render step.

    Translation can take longer than Cloudflare's ~100 s request limit, so the
    settings endpoint queues this instead of waiting.
    """
    session = get_session(job_id)
    if not session:
        return
    try:
        translated = translate_workspace(session, target_lang, job_id=job_id)
    except Exception as exc:
        error = exc.as_error_dict() if hasattr(exc, "as_error_dict") else {
            "code": "translation_error", "message": str(exc), "stage": "translation", "retryable": True,
        }
        update_session(job_id, {
            "error": error,
            "status": "Falha na tradução do workspace",
            "current_step": "translation_failed",
            "progress": 100,
        })
        add_log(job_id, f"Falha na tradução do workspace: {exc}")
        return
    if translated:
        dropped = set(drop_component_ids)
        update_session(job_id, {
            "artifacts": translated["artifacts"],
            "components": {key: code for key, code in translated["components"].items() if key not in dropped},
        })
    follow_up(job_id)


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
            with pd.ExcelFile(path) as workbook:
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
    except Exception as exc:
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


def save_uploaded_files(files: list[UploadFile], destination_dir: str | os.PathLike | None = None) -> list:
    """Validate and save uploads into the report's uploads folder (legacy: the shared one)."""
    destination_dir = str(destination_dir or UPLOAD_DIR)
    os.makedirs(destination_dir, exist_ok=True)
    saved_files = []
    names = [str(file.filename or "").casefold() for file in files]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="Each uploaded file must have a unique filename")
    for file in files:
        validate_upload(file)
    created_paths = []
    total_bytes = 0
    for file in files:
        file_ext = validate_upload(file)
        data_path = os.path.join(destination_dir, f"{uuid.uuid4()}{file_ext}")
        created_paths.append(data_path)
        try:
            size_bytes = copy_upload_with_limit(file, data_path)
            total_bytes += size_bytes
            if total_bytes > MAX_REQUEST_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="The files in one upload exceed the total size limit")
            validation = validate_saved_upload(data_path, file_ext, file.filename, file.content_type)
        except Exception:
            for created_path in created_paths:
                try:
                    os.remove(created_path)
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
    files_map = pipeline_build_files_map(session)
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
