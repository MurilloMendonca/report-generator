"""Chat refinement: routing feedback, translating the workspace, editing components."""

import ast
import json
import os
import time
from copy import deepcopy

from pydantic import ValidationError

from app.models.refinement import RefinementDecision, SettingsChanges
from app.utils.component_library import CHART_STYLES, VISUAL_THEMES
from app.utils.component_renderers import render_function_suffix, runtime_api_prompt_reference, supports_library_renderer
from app.utils.layout import normalize_template
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.services.llm import client
from app.services.llm.client import llm_operation_context
from app.services.llm.components import VISUAL_COMPONENT_TYPES
from app.services.llm.errors import LLMContractError
from app.services.llm.parsing import _parse_contract_json, _raw_excerpt, parse_llm_translation_payload, strip_code_fence
from app.services.llm.prompts import load_prompt


def validate_refinement_decision(payload: dict, session: dict) -> dict:
    decision = RefinementDecision.model_validate(payload).model_dump(exclude_none=True)
    artifacts = {item["id"]: item for item in session.get("artifacts", [])}
    targets = decision["component_ids"]
    if set(targets) - artifacts.keys():
        raise ValueError("Refinement references an unknown component ID")
    settings = decision["settings_changes"]
    if settings.get("visual_theme_id") is not None and settings["visual_theme_id"] not in VISUAL_THEMES:
        raise ValueError("Unknown visual theme")
    if settings.get("chart_style_id") is not None and settings["chart_style_id"] not in CHART_STYLES:
        raise ValueError("Unknown chart style")
    if decision["intent"] == "visual":
        selected = targets or list(artifacts)
        visuals = [artifacts[key] for key in selected if artifacts[key].get("type") in VISUAL_COMPONENT_TYPES]
        if not visuals or (targets and len(visuals) != len(targets)):
            raise ValueError("Visual changes require existing visual components")
    if decision["intent"] == "component" and any(artifacts[key].get("type") == "metric" for key in targets):
        raise ValueError("Metrics must be changed through data or report intent")
    if decision["intent"] == "layout":
        layout = {item.get("id"): item for item in session.get("template", [])}
        updates = [item["id"] for item in decision["layout_updates"]]
        if len(updates) != len(set(updates)) or set(updates) - layout.keys():
            raise ValueError("Layout updates must reference unique existing layout blocks")
        if targets and set(updates) != set(targets):
            raise ValueError("Layout updates must match the declared component scope")
        for update in decision["layout_updates"]:
            block = {**layout[update["id"]], **update}
            if not (0 <= block["x"] <= 210 - block["w"] and 30 <= block["y"] <= 285 - block["h"]):
                raise ValueError("Layout update extends outside the printable page")
    if decision["intent"] == "data" and not any(
        os.path.splitext(file.get("path", ""))[1].lower() in {".csv", ".xls", ".xlsx"}
        for file in session.get("files", [])
    ):
        raise ValueError("Data preprocessing requires a tabular source")
    return decision


def parse_llm_refinement_decision(text: str, session: dict) -> dict:
    payload = _parse_contract_json(text, dict, "refinement_routing", "decision")
    try:
        return validate_refinement_decision(payload, session)
    except (ValidationError, ValueError) as exc:
        raise LLMContractError(
            "refinement_routing", "Refinement decision failed schema or scope validation",
            field="decision", raw_excerpt=_raw_excerpt(text), cause=exc,
        ) from exc


def classify_refinement(session: dict, prompt: str, job_id: str | None = None) -> dict:
    provider = session.get("provider", DEFAULT_PROVIDER)
    model_name = session.get("model_name", DEFAULT_MODEL_NAME)
    settings = session.get("settings") or {}
    # Only names/schema and report structure are needed for routing, never raw rows or scripts.
    context = {
        "feedback": prompt,
        "language": settings.get("language", "Portuguese"),
        "accepted_goal": str(session.get("current_goal") or session.get("initial_prompt") or "")[-4000:],
        "pending_clarification": session.get("pending_refinement") or {},
        "recent_messages": [
            {"role": item.get("role"), "content": str(item.get("content") or "")[:1000]}
            for item in session.get("refinement_messages", [])[-6:]
        ],
        "components": [
            {
                **{key: artifact.get(key) for key in ("id", "indicator", "type", "component_preset_id", "visual_options", "columns")},
                "binding_roles": (artifact.get("data_binding") or {}).get("roles", {}),
            }
            for artifact in session.get("artifacts", [])
        ],
        "layout_mm": session.get("template", []),
        "settings": {key: value for key, value in settings.items() if key in SettingsChanges.model_fields},
        "sources": [
            {"filename": file.get("original_filename"), "columns": [
                column.get("name") if isinstance(column, dict) else column
                for column in (file.get("artifact") or {}).get("columns", [])
            ]}
            for file in session.get("files", [])
        ],
        "themes": [
            {key: theme.get(key) for key in ("id", "description", "palette")}
            for theme in VISUAL_THEMES.values()
        ],
        "chart_styles": [{"id": style["id"], "description": style["description"]} for style in CHART_STYLES.values()],
    }
    with llm_operation_context(
        job_id=job_id or session.get("job_id"), operation="refinement_routing",
        settings=settings, provider=provider, model_name=model_name,
    ):
        response = client.call_llm(
            provider, model_name, load_prompt("REFINE_MODULAR.md"),
            json.dumps(context, ensure_ascii=False) + "\n\nDecision JSON schema:\n"
            + json.dumps(RefinementDecision.model_json_schema(), ensure_ascii=False),
        )
    return parse_llm_refinement_decision(response, session)


def translate_workspace(session: dict, target_lang: str, job_id: str | None = None) -> dict:
    provider = session.get('provider', DEFAULT_PROVIDER)
    model_name = session.get('model_name')
    with llm_operation_context(job_id=job_id or session.get("job_id"), operation="workspace_translation", settings=session.get("settings"), provider=provider, model_name=model_name):
        system_instruction = load_prompt("TRANSLATE_WORKSPACE.md")

        workspace_data = {
            "target_language": target_lang,
            "artifacts": session.get('artifacts', []),
            "components": session.get('components', {})
        }

        user_prompt = f"Workspace to Translate:\n{json.dumps(workspace_data, indent=2)}"

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return parse_llm_translation_payload(client.call_llm(provider, model_name, system_instruction, user_prompt), provider, model_name)
            except LLMContractError as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            if isinstance(last_error, LLMContractError):
                raise LLMContractError(
                    last_error.stage,
                    str(last_error),
                    field=last_error.field,
                    provider=provider,
                    model_name=model_name,
                    raw_excerpt=last_error.raw_excerpt,
                    cause=last_error.cause or last_error,
                ) from last_error
            raise LLMContractError(
                "workspace_translation",
                "Failed to translate workspace after retries",
                provider=provider,
                model_name=model_name,
                raw_excerpt=str(last_error),
                cause=last_error,
            ) from last_error
        return None


def _refine_component_function(session: dict, artifact: dict, instruction: str, job_id: str | None) -> str:
    component_id = artifact["id"]
    suffix = render_function_suffix(component_id)
    runtime_reference = ""
    if artifact.get("type") in VISUAL_COMPONENT_TYPES:
        runtime_reference = (
            "\n\nRuntime Renderer API Reference:\n"
            + runtime_api_prompt_reference(artifact.get("component_preset_id"))
            + "\nThe returned function is the primary renderer. Prefer a short adapter calling the runtime API."
        )
    prompt = (
        f"Target language: {session.get('settings', {}).get('language', 'Portuguese')}\n"
        f"Update FUNCTION render_{suffix} for this instruction:\n{instruction}\n\n"
        f"Target component JSON:\n{json.dumps(artifact, ensure_ascii=False)}\n\n"
        f"Current code:\n{session.get('components', {}).get(component_id, 'No custom function; currently library-rendered.')}\n\n"
        f"Data schema:\n{json.dumps([file['artifact'] for file in session.get('files', []) if file.get('artifact')], ensure_ascii=False)}"
        f"{runtime_reference}\nReturn ONLY Python code. Preserve unrelated behavior."
    )
    with llm_operation_context(
        job_id=job_id or session.get("job_id"), operation="refinement_component",
        settings=session.get("settings"), provider=session.get("provider"), model_name=session.get("model_name"),
    ):
        code = strip_code_fence(client.call_llm(
            session.get("provider", DEFAULT_PROVIDER), session.get("model_name", DEFAULT_MODEL_NAME),
            "Expert Python developer specialized in report components.", prompt,
        ).strip(), "python")
    try:
        tree = ast.parse(code)
        if not any(isinstance(node, ast.FunctionDef) and node.name == f"render_{suffix}" for node in tree.body):
            raise ValueError("Missing target renderer function")
    except (SyntaxError, ValueError) as exc:
        raise LLMContractError("refinement_component", "Invalid component function", field=component_id, cause=exc) from exc
    return code


def refine_session_modular(
    session: dict, new_prompt: str, job_id: str | None = None, decision: dict | None = None,
) -> dict:
    # A caller may supply the already classified decision; never classify twice.
    if decision is None:
        decision = classify_refinement(session, new_prompt, job_id=job_id)
    decision = validate_refinement_decision(decision, session)
    state = deepcopy(session)
    state.update(needs_script_update=False, needs_full_regeneration=False, needs_data_preprocessing=False)
    intent = decision["intent"]
    instruction = decision["instruction"]
    if intent == "clarify":
        state["clarification_question"] = decision["clarification_question"]
        return state

    settings_changes = decision["settings_changes"]
    settings = dict(state.get("settings") or {})
    old_language = settings.get("language")
    if "visual_theme_id" in settings_changes and "chart_palette" not in settings_changes:
        settings["chart_palette"] = []
    settings.update(settings_changes)
    state["settings"] = settings

    if intent in {"data", "report"}:
        state["needs_full_regeneration"] = True
        state["needs_data_preprocessing"] = intent == "data"
        return state
    if intent == "settings":
        if settings.get("language") and old_language and settings["language"] != old_language:
            translated = translate_workspace(state, settings["language"], job_id=job_id or session.get("job_id"))
            if not translated:
                raise LLMContractError("workspace_translation", "Translation returned no workspace")
            state["artifacts"] = translated["artifacts"]
            state["components"] = translated["components"]
    elif intent == "layout":
        updates = {item["id"]: item for item in decision["layout_updates"]}
        state["template"] = normalize_template([
            {**block, **updates.get(block["id"], {})} for block in state.get("template", [])
        ])
    elif intent == "visual":
        targets = set(decision["component_ids"]) or {
            artifact["id"] for artifact in state.get("artifacts", [])
            if artifact.get("type") in VISUAL_COMPONENT_TYPES
        }
        if decision["scope"] == "report":
            instructions = [line for line in str(settings.get("visual_instructions") or "").splitlines() if line.strip()]
            if f"- {instruction}" not in instructions:
                instructions.append(f"- {instruction}")
            settings["visual_instructions"] = "\n".join(instructions)
        for artifact in state.get("artifacts", []):
            if artifact["id"] not in targets:
                continue
            options = dict(artifact.get("visual_options") or {})
            if settings_changes.keys() & {"visual_theme_id", "chart_palette"}:
                for key in ("color", "line_color", "bar_color"):
                    options.pop(key, None)
            changes = dict(decision["visual_options"])
            if "labels" in changes:
                changes["labels"] = {**options.get("labels", {}), **changes["labels"]}
            options.update(changes)
            artifact["visual_options"] = options
            if artifact.get("render_mode") == "library" and supports_library_renderer(artifact):
                state.setdefault("components", {}).pop(artifact["id"], None)
            else:
                state.setdefault("components", {})[artifact["id"]] = _refine_component_function(state, artifact, instruction, job_id)
                artifact["render_mode"] = "generated_code"
    elif intent == "component":
        for artifact in state.get("artifacts", []):
            if artifact["id"] in decision["component_ids"]:
                state.setdefault("components", {})[artifact["id"]] = _refine_component_function(state, artifact, instruction, job_id)
                if artifact.get("type") in VISUAL_COMPONENT_TYPES:
                    artifact["render_mode"] = "generated_code"

    state["needs_script_update"] = True
    return state
