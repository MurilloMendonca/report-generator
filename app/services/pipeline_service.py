import json
import os
import time
import traceback
import unicodedata
import uuid

import pandas as pd

from app.services.executor_service import execute_script
from app.services.llm_service import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER,
    assemble_script,
    calculate_metric_value,
    condense_texts_to_fit,
    dataframe_load_line,
    fix_script,
    generate_all_texts,
    generate_artifacts,
    generate_component_code,
    generate_data_filter,
    generate_report_title,
    generate_template,
    slot_by_artifact_id,
)
from app.services.session_manager import add_log, get_session, update_session
from app.utils.data_analyzer import extract_metadata
from app.utils.artifact_validation import validate_report_plan
from app.utils.layout import normalize_template
from app.utils.template_library import (
    fit_artifacts_to_template,
    select_layout_template,
    template_constraints,
)

FILTERED_DIR = "workspace/filtered"
SCRIPT_DIR = "workspace/scripts"
REPORT_DIR = "workspace/reports"
VISUAL_TYPES = {'graphic', 'bar', 'line', 'pie', 'scatter', 'table', 'chart'}
DEFAULT_SETTINGS = {
    "title": "",
    "font": "Arial",
    "font_size": 10,
    "alignment": "J",
    "line_height": 5,
    "language": "Portuguese"
}
DEFAULT_EXPLORATORY_PROMPT = (
    "Crie um relatório exploratório dos dados enviados. Identifique padrões relevantes, "
    "tendências, outliers, comparações importantes, possíveis relações entre variáveis e "
    "conclusões acionáveis. Priorize achados que possam ser sustentados pelos dados e "
    "inclua contexto suficiente para que o leitor entenda os principais insights sem uma "
    "pergunta específica do usuário."
)

for directory in [FILTERED_DIR, SCRIPT_DIR, REPORT_DIR]:
    os.makedirs(directory, exist_ok=True)


def processed_path(file_info: dict) -> str | None:
    return file_info.get("preprocessed_path") or file_info.get("filtered_path")


def preprocessing_description(file_info: dict) -> str:
    return file_info.get("preprocessing_description") or file_info.get("filter_description") or ""


def preprocessing_needed(state: dict | None) -> bool:
    return bool(state and (state.get("needs_preprocessing") or state.get("needs_filter")))


def build_files_map(session: dict) -> dict:
    return {
        f['original_filename']: os.path.abspath(processed_path(f) or f['path'])
        for f in session.get('files', [])
    }


def metadata_json(session: dict) -> str:
    return json.dumps([f['artifact'] for f in session.get('files', []) if f.get('artifact')], indent=2)


def raw_metadata_json(session: dict) -> str:
    return json.dumps([f.get('raw_artifact') or f.get('artifact') for f in session.get('files', []) if f.get('raw_artifact') or f.get('artifact')], indent=2)


def normalize_filter_prompt(prompt: str) -> str:
    text = unicodedata.normalize("NFKD", prompt or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def should_consider_data_filter(prompt: str, session: dict = None) -> bool:
    if session and preprocessing_needed(session.get("data_preprocessing") or session.get("data_filter")):
        return True
    normalized = normalize_filter_prompt(prompt)
    keywords = [
        "filtrar", "filtro", "excluir", "exclua", "remover", "remova",
        "retirar", "retire", "ignorar", "ignore", "desconsiderar",
        "desconsidere", "nao incluir", "nao inclua", "nao considerar",
        "nao considere", "nao usar", "nao use", "nenhum", "nenhuma",
        "apenas", "somente", "so ", "sem ", "exceto", "limitar",
        "restringir", "focar", "foco em", "considerar so",
        "considerar apenas", "incluir apenas", "inclua apenas",
        "preprocess", "pre-process", "pre process", "preprocessar",
        "pre-processar", "pre processamento", "pre-processamento",
        "transformar", "transforme", "preparar", "prepare",
        "limpar", "limpe", "normalizar", "normalize", "padronizar",
        "agrupar", "agrupe", "agregado", "agregar", "resumir por",
        "group by", "grouped by", "aggregate", "summarize by",
        "nova coluna", "coluna calculada", "criar coluna",
        "adicione uma coluna", "adicionar uma coluna", "calcular coluna",
        "calculated column", "new column", "derive column",
        "keep only", "only", "exclude", "remove", "ignore", "filter",
        "without", "except", "focus on", "restrict", "do not include",
        "don't include"
    ]
    return any(keyword in normalized for keyword in keywords)


def has_data_filter_decision(data_filter: dict | None) -> bool:
    if not data_filter:
        return False
    return bool(
        preprocessing_needed(data_filter)
        or data_filter.get("description")
        or data_filter.get("code")
    )


def supported_tabular_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in {".csv", ".xls", ".xlsx"}


def read_tabular_preview(path: str) -> tuple[int, set]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(path)
    elif ext in {".xls", ".xlsx"}:
        df = pd.read_excel(path)
    else:
        return 0, set()
    return int(len(df)), {str(column) for column in df.columns}


def preprocessing_impact(original_path: str, processed_path_value: str) -> dict:
    before_rows, before_columns = read_tabular_preview(original_path)
    after_rows, after_columns = read_tabular_preview(processed_path_value)
    removed_rows = max(0, before_rows - after_rows)
    return {
        "rows_before": before_rows,
        "rows_after": after_rows,
        "rows_removed": removed_rows,
        "rows_removed_ratio": round(removed_rows / before_rows, 4) if before_rows else 0,
        "columns_before": len(before_columns),
        "columns_after": len(after_columns),
        "columns_added": sorted(after_columns - before_columns),
        "columns_removed": sorted(before_columns - after_columns),
    }


def apply_data_filter(job_id: str, session: dict, data_filter: dict) -> list:
    files = session.get("files", [])
    output_paths = {}
    impacts = {}
    setup_lines = ["import pandas as pd", "import numpy as np", "dataframes = {}"]

    for file_info in files:
        source_path = file_info.get("path")
        if not source_path or not supported_tabular_path(source_path):
            continue
        setup_line = dataframe_load_line(file_info["original_filename"], os.path.abspath(source_path))
        if setup_line:
            setup_lines.append(setup_line)
            output_path = os.path.abspath(os.path.join(FILTERED_DIR, f"{job_id}_{uuid.uuid4().hex}_preprocessed.csv"))
            output_paths[file_info["original_filename"]] = output_path

    if not output_paths:
        return files

    script = "\n".join(setup_lines)
    script += "\n\n"
    script += data_filter.get("code", "")
    script += "\n\n"
    script += f"output_paths = {json.dumps(output_paths, ensure_ascii=False)}\n"
    script += "if 'preprocess_dataframes' in globals():\n"
    script += "    processed = preprocess_dataframes(dataframes)\n"
    script += "else:\n"
    script += "    processed = filter_dataframes(dataframes)\n"
    script += "for name, path in output_paths.items():\n"
    script += "    df = processed.get(name, dataframes.get(name))\n"
    script += "    if df is None:\n"
    script += "        continue\n"
    script += "    df.to_csv(path, index=False)\n"

    script_path = os.path.join(SCRIPT_DIR, f"preprocess_{job_id}.py")
    result = execute_script(script, script_path)
    if not result["success"]:
        raise RuntimeError(result["stderr"] or "Falha ao aplicar pré-processamento de dados")

    for file_info in files:
        filtered_path = output_paths.get(file_info.get("original_filename"))
        if filtered_path and os.path.exists(filtered_path):
            file_info["preprocessed_path"] = filtered_path
            file_info["preprocessing_description"] = data_filter.get("description", "")
            file_info["filtered_path"] = filtered_path
            file_info["filter_description"] = data_filter.get("description", "")
            file_info["artifact"] = extract_metadata(filtered_path, file_info["original_filename"])
            impact = preprocessing_impact(file_info["path"], filtered_path)
            file_info["preprocessing_impact"] = impact
            file_info["filter_impact"] = impact
            impacts[file_info["original_filename"]] = impact
        elif file_info.get("raw_artifact"):
            file_info["artifact"] = file_info["raw_artifact"]
            file_info.pop("preprocessed_path", None)
            file_info.pop("preprocessing_description", None)
            file_info.pop("filtered_path", None)
            file_info.pop("filter_description", None)
            file_info.pop("preprocessing_impact", None)
            file_info.pop("filter_impact", None)

    data_filter["impact"] = impacts

    return files


def clear_data_filter_state(files: list) -> list:
    for file_info in files:
        file_info.pop("preprocessed_path", None)
        file_info.pop("preprocessing_description", None)
        file_info.pop("filtered_path", None)
        file_info.pop("filter_description", None)
        file_info.pop("preprocessing_impact", None)
        file_info.pop("filter_impact", None)
        if file_info.get("raw_artifact"):
            file_info["artifact"] = file_info["raw_artifact"]
    return files


def text_component_code(artifact: dict, text_content: str) -> str:
    safe_text = json.dumps(text_content)
    safe_title = json.dumps(artifact.get('indicator', ''))
    return (
        f"def render_{artifact['id']}(pdf, dataframes, x, y, w, h):\n"
        f"    render_text_block(pdf, {safe_text}, x, y, w, h, title={safe_title})"
    )


def run_pipeline(job_id: str):
    session = get_session(job_id)
    provider = session.get('provider', DEFAULT_PROVIDER)
    model_name = session.get('model_name', DEFAULT_MODEL_NAME)

    try:
        files = session.get('files', [])
        any_new_artifact = False
        for i, file_info in enumerate(files):
            if not file_info.get('raw_artifact'):
                raw_artifact = extract_metadata(file_info['path'], file_info['original_filename'])
                files[i]['raw_artifact'] = raw_artifact
                if not processed_path(file_info):
                    files[i]['artifact'] = raw_artifact
                any_new_artifact = True
        if any_new_artifact:
            session = update_session(job_id, {"files": files}) or session
            files = session.get("files", files)

        data_filter = session.get("data_preprocessing") or session.get("data_filter", {})
        has_filter_decision = has_data_filter_decision(data_filter)
        filter_prompt = session.get('current_goal', session['initial_prompt'])
        should_filter = should_consider_data_filter(filter_prompt, session)
        if should_filter and (any_new_artifact or not has_filter_decision):
            add_log(job_id, "Avaliando pré-processamento de dados solicitado...")
            data_filter = generate_data_filter(raw_metadata_json({"files": files}), filter_prompt, provider, model_name, settings=session.get('settings'))
            session = update_session(job_id, {"data_preprocessing": data_filter, "data_filter": data_filter}) or session
            has_filter_decision = has_data_filter_decision(data_filter)

        needs_filter_apply = preprocessing_needed(data_filter) and (
            any_new_artifact or any(supported_tabular_path(f.get("path", "")) and not processed_path(f) for f in files)
        )
        if needs_filter_apply:
            add_log(job_id, f"Aplicando pré-processamento de dados: {data_filter.get('description', 'transformação ativa')}")
            files = apply_data_filter(job_id, session, data_filter)
            session = update_session(job_id, {"files": files, "data_preprocessing": data_filter, "data_filter": data_filter}) or session
            files = session.get("files", files)
            any_new_artifact = True
        elif has_filter_decision and not preprocessing_needed(data_filter):
            files = clear_data_filter_state(files)
            session = update_session(job_id, {"files": files, "data_preprocessing": data_filter, "data_filter": data_filter}) or session

        all_metadata = metadata_json({"files": files})
        files_map = build_files_map({"files": files})

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
                    validation_context = template_constraints(selected_template) if selected_template else None
                    artifacts, validation_issues = validate_report_plan(
                        artifacts,
                        [f.get("artifact") for f in files if f.get("artifact")],
                        validation_context,
                    )
                    update_session(job_id, {"artifact_validation": validation_issues})
                    if validation_issues:
                        add_log(job_id, f"Plano de indicadores validado com {len(validation_issues)} aviso(s).")
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

        metrics_store = session.get('calculated_metrics', {})
        metric_artifacts = [a for a in artifacts if a.get('type') == 'metric']
        for art in metric_artifacts:
            if art['id'] not in metrics_store or any_new_artifact:
                add_log(job_id, f"Calculando métrica: {art['indicator']}...")
                val = calculate_metric_value(all_metadata, art, files_map, provider, model_name)
                metrics_store[art['id']] = val
        update_session(job_id, {"calculated_metrics": metrics_store})

        visible_artifacts = [a for a in artifacts if a.get('type') in VISUAL_TYPES or a.get('type') == 'text']
        template = session.get('template', [])
        if not template or len(template) != len(visible_artifacts):
            template_name = selected_template.get("name") if selected_template else "fallback"
            add_log(job_id, f"Aplicando template de layout: {template_name}...")
            template = generate_template(visible_artifacts, provider, model_name, template_id=selected_template_id)
            update_session(job_id, {"template": normalize_template(template)})

        components = session.get('components', {})
        blocks_by_id = slot_by_artifact_id(template)

        graphic_artifacts = [a for a in artifacts if a.get('type') in VISUAL_TYPES]
        for art in graphic_artifacts:
            if art['id'] not in components:
                add_log(job_id, f"Gerando gráfico: {art['indicator']}...")
                code = generate_component_code(
                    all_metadata,
                    art,
                    provider,
                    model_name,
                    calculated_metrics=metrics_store,
                    settings=session.get('settings'),
                    layout_slot=blocks_by_id.get(art.get("id")),
                )
                components[art['id']] = code

        text_artifacts = [a for a in artifacts if a.get('type') == 'text']
        needs_text_refresh = any_new_artifact or \
                             any(art['id'] not in components for art in text_artifacts) or \
                             any(art['id'] in components and "render_text_block" not in components[art['id']] for art in text_artifacts)

        if text_artifacts and needs_text_refresh:
            add_log(job_id, f"Gerando narrativas consistentes ({len(text_artifacts)})...")
            texts_map = generate_all_texts(
                all_metadata,
                text_artifacts,
                metrics_store,
                provider,
                model_name,
                settings=session.get('settings'),
                template=template,
            )
            condensed_texts_map = condense_texts_to_fit(texts_map, text_artifacts, template, provider, model_name, settings=session.get('settings'))
            if condensed_texts_map != texts_map:
                add_log(job_id, "Condensando narrativas para caber nos blocos do layout...")
                texts_map = condensed_texts_map

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
            add_log(job_id, "Erro detectado. Autocorreção iniciada...")
            all_metadata = metadata_json(session)
            fixed_code = fix_script(session['script'], result['stderr'], all_metadata, session.get('provider'), session.get('model_name'))
            update_session(job_id, {"script": fixed_code, "retry_count": retries + 1})
            run_execution(job_id)
        else:
            add_log(job_id, f"Falha após {max_retries} tentativas.")
