"""Edits confined to one report component, with background rendering and recovery."""
import ast
from copy import deepcopy

from app.services.llm.client import llm_operation_context
from app.services.llm.components import generate_component_code, slot_by_artifact_id
from app.services.llm.refinement import classify_refinement, refine_session_modular
from app.services.llm.texts import condense_texts_to_fit, generate_all_texts
from app.services.pipeline_service import (
    VISUAL_TYPES, metadata_json, run_script_generation, serialized_pipeline, text_component_code,
)
from app.services.session_manager import (
    add_log, commit_component_edit, get_report_owner, get_session, preserve_report_on_failure,
    set_component_edit_status, set_session_stage,
)
from app.services.web_helpers import validate_component_code
from app.utils.artifact_validation import validate_report_plan
from app.utils.component_renderers import supports_library_renderer
from app.utils.narrative_evidence import ground_narrative


def component_text(code: str) -> str:
    """Read the literal text from our text renderer without executing generated code."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "render_text_block":
            value = node.args[1] if len(node.args) > 1 else next((item.value for item in node.keywords if item.arg == "text"), None)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return ""


def component_texts(session: dict) -> dict:
    return {item["id"]: component_text(session.get("components", {}).get(item["id"], ""))
            for item in session.get("artifacts", []) if item.get("type") == "text"}


def _edit_component(session: dict, component_id: str, payload: dict) -> tuple[dict, str | None]:
    original = next(item for item in session["artifacts"] if item["id"] == component_id)
    artifact = {**original, **(payload.get("artifact") or {}), "id": component_id}
    if artifact.get("type") not in VISUAL_TYPES | {"text"}:
        raise ValueError("Edite cálculos de métricas em Métricas e Dados.")
    metadata_items = [file["artifact"] for file in session.get("files", []) if file.get("artifact")]
    artifact = validate_report_plan([artifact], metadata_items)[0][0]
    session = deepcopy(session)
    session["artifacts"] = [artifact if item["id"] == component_id else item for item in session["artifacts"]]
    old_code = session.get("components", {}).get(component_id, "")
    code = payload.get("code")
    if code is not None:
        session.setdefault("components", {})[component_id] = code
    instruction = str(payload.get("instruction") or "").strip()
    provider, model = session.get("provider"), session.get("model_name")
    metadata = metadata_json(session)
    slot = slot_by_artifact_id(session.get("template", [])).get(component_id)

    if artifact["type"] == "text":
        text = payload.get("text")
        if text is None:
            text = component_text(old_code)
        if payload.get("text") is not None and not str(text).strip() and not instruction and not payload.get("regenerate"):
            raise ValueError("O texto não pode ficar vazio. Escreva o conteúdo ou solicite uma revisão à IA.")
        if instruction or payload.get("regenerate") or not text:
            target = {**artifact, "current_text": text, "requested_revision": instruction or "Reescreva com clareza, preservando fatos e objetivo."}
            texts = generate_all_texts(metadata, [target], session.get("calculated_metrics", {}), provider, model,
                                       settings=session.get("settings"), template=session.get("template"), job_id=session["job_id"],
                                       report_goal=session.get("current_goal") or session.get("initial_prompt", ""), report_artifacts=session["artifacts"])
            texts = condense_texts_to_fit(texts, [target], session.get("template", []), provider, model,
                                         settings=session.get("settings"), job_id=session["job_id"], calculated_metrics=session.get('calculated_metrics', {}))
            text = texts[component_id]
        if not str(text).strip():
            raise ValueError("O texto do componente não pode ficar vazio.")
        text = ground_narrative(artifact, text, session.get('calculated_metrics', {}))
        code = text_component_code(artifact, text)
    elif instruction:
        # Supply an explicit selection; the model must not expand its scope.
        decision = classify_refinement(session, instruction, job_id=session["job_id"], component_id=component_id)
        if decision["intent"] == "clarify":
            raise ValueError(decision["clarification_question"])
        if decision["scope"] != "components" or decision["component_ids"] != [component_id] or decision["intent"] not in {"visual", "component"}:
            raise ValueError("Este editor altera apenas o componente selecionado. Use o chat do relatório para mudar dados, layout ou configurações gerais.")
        refined = refine_session_modular(session, instruction, job_id=session["job_id"], decision=decision)
        artifact = next(item for item in refined["artifacts"] if item["id"] == component_id)
        code = refined.get("components", {}).get(component_id)
    elif code is None:
        changed = artifact != original or payload.get("regenerate")
        if artifact.get("render_mode") == "library" and supports_library_renderer(artifact):
            code = None
        elif changed or not old_code:
            if old_code and not payload.get("regenerate"):
                instruction = "Aplique os campos e opções atualizados do componente, preservando os cálculos e comportamentos existentes que não foram alterados. Respeite data_binding e visual_options."
                refined = refine_session_modular(session, instruction, job_id=session["job_id"], decision={
                    "intent": "component", "scope": "components", "component_ids": [component_id], "instruction": instruction,
                })
                artifact = next(item for item in refined["artifacts"] if item["id"] == component_id)
                code = refined["components"][component_id]
            else:
                code = generate_component_code(metadata, artifact, provider, model, calculated_metrics=session.get("calculated_metrics"),
                                               settings=session.get("settings"), layout_slot=slot, job_id=session["job_id"])
        else:
            code = old_code

    if code:
        validate_component_code(component_id, code)
    validated, issues = validate_report_plan([artifact], [file["artifact"] for file in session.get("files", []) if file.get("artifact")])
    binding_issues = [issue for issue in issues if issue.get("code", "").startswith(("data_binding", "invalid_data_binding"))]
    if binding_issues and artifact.get("render_mode") == "library":
        raise ValueError("Revise a fonte e as colunas selecionadas: " + "; ".join(str(issue.get("message") or issue["code"]) for issue in binding_issues))
    return validated[0], code


@preserve_report_on_failure
def run_component_edit(job_id: str, token: str, payload: dict):
    with serialized_pipeline(job_id, "component_edit"):
        session = get_session(job_id)
        operation = (session or {}).get("component_edit") or {}
        if operation.get("id") != token:
            return
        component_id = operation["component_id"]
        try:
            set_component_edit_status(job_id, token, "running", "Aplicando as alterações ao componente selecionado...")
            set_session_stage(job_id, "component_edit", 25, error={})
            with llm_operation_context(job_id=job_id, user_id=get_report_owner(job_id), operation="component_edit", settings=session.get("settings")):
                artifact, code = _edit_component(session, component_id, payload)
                commit_component_edit(job_id, token, artifact, code)
                add_log(job_id, f"Componente atualizado: {artifact['indicator']}. Renderizando a nova versão.")
                run_script_generation(job_id)
            latest = get_session(job_id) or {}
            if latest.get("current_step") == "failed":
                raise ValueError((latest.get("error") or {}).get("message") or "Não foi possível renderizar o componente.")
            set_component_edit_status(job_id, token, "completed", "Componente atualizado. A nova versão está disponível na prévia e no relatório.")
        except Exception as exc:
            error = exc.as_error_dict() if hasattr(exc, "as_error_dict") else {"code": "component_edit_error", "message": str(exc), "stage": "component_edit", "retryable": True}
            set_session_stage(job_id, "failed", 100, error)
            set_component_edit_status(job_id, token, "failed", str(error.get("message") or exc))
            add_log(job_id, f"Falha ao editar o componente {component_id}: {error['message']}")
