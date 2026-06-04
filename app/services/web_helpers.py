import ast
import mimetypes
import os
import time
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services.pipeline_service import DEFAULT_EXPLORATORY_PROMPT, DEFAULT_SETTINGS
from app.services.llm_service import (
    LLMBudgetError,
    LLMContractError,
    LLMTimeoutError,
    assemble_script,
    dataframe_load_line,
    generate_exploration_script,
    refine_session_modular,
    resolve_llm_config,
    strip_code_fence,
    translate_workspace,
)
from app.services.pipeline_service import (
    FILTERED_DIR,
    REPORT_DIR,
    SCRIPT_DIR,
    VISUAL_TYPES,
    add_log,
    build_files_map as pipeline_build_files_map,
    clear_data_filter_state,
    metadata_json,
    normalize_filter_prompt,
    run_pipeline,
    run_script_generation,
    should_consider_data_filter,
)
from app.services.session_manager import (
    create_session,
    get_session,
    list_recent_sessions,
    set_session_stage,
    update_session,
)
from app.utils.layout import normalize_template
from app.utils.template_library import (
    delete_layout_template,
    load_layout_templates,
    upsert_layout_template,
)

UPLOAD_DIR = "workspace/uploads"
ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xls", ".xlsx", ".pdf"}
MAX_UPLOAD_BYTES = int(os.getenv("PDF_REPORT_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
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
        add_log(job_id, f"Falha de limite LLM no refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", f"Não consegui aplicar o refinamento: {str(exc)}", "error")
        raise
    except LLMContractError as exc:
        add_log(job_id, f"Falha de contrato LLM no refinamento: {str(exc)}")
        append_refinement_message(job_id, "assistant", f"Não consegui aplicar o refinamento: {str(exc)}", "error")
        raise


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


def save_uploaded_files(files: list[UploadFile]) -> list:
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
