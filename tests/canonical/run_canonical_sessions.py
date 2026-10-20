import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from app.services.pipeline_service import DEFAULT_EXPLORATORY_PROMPT, DEFAULT_SETTINGS, run_pipeline, run_script_generation, summarize_render_manifest
from app.services.web_helpers import append_refinement_message, process_chat_refinement
from app.services.session_manager import add_log, create_session, get_session, update_session
from app.services.crypto import dev_server_keys_enabled
from app.services.llm.client import llm_operation_context
from app.services.llm.credentials import resolve_api_key
from app.services.llm.providers.registry import ADAPTERS
from app.services.redaction import install_log_redaction
from app.utils.model_config import DEFAULT_MODEL_BY_PROVIDER, current_model_name
from app.utils.report_paths import ReportPaths


CANONICAL_DIR = Path(__file__).parent
RESULTS_DIR = CANONICAL_DIR / "results"
GENERATED_PDFS_DIR = CANONICAL_DIR / "generated_pdfs"


def load_playbook() -> list[dict]:
    with open(CANONICAL_DIR / "playbook.json", "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_fixtures():
    from tests.canonical.generate_fixtures import FIXTURE_DIR, education_outcomes, hr_retention, logistics_quality, sales_ops

    if not FIXTURE_DIR.exists():
        sales_ops()
        hr_retention()
        logistics_quality()
        education_outcomes()


def create_canonical_session(case: dict, owner_id=None) -> str:
    job_id = f"canonical-{case['id']}-{uuid.uuid4().hex[:8]}"
    owner_id = owner_id or canonical_owner_id()
    paths = ReportPaths.for_new_report(job_id, str(owner_id)).ensure()

    files = []
    for relative_path in case["files"]:
        source = CANONICAL_DIR / relative_path
        suffix = source.suffix
        target = ROOT / paths.uploads / f"{uuid.uuid4()}{suffix}"
        shutil.copy2(source, target)
        files.append({
            "original_filename": source.name,
            "path": str(target.relative_to(ROOT)),
            "artifact": None,
        })

    settings = DEFAULT_SETTINGS.copy()
    settings["selected_template_id"] = case["template_id"]

    user_prompt = (case.get("initial_prompt") or "").strip()
    effective_prompt = user_prompt or DEFAULT_EXPLORATORY_PROMPT

    create_session(job_id, {
        "job_id": job_id,
        "status": "Iniciando teste canônico...",
        "logs": [f"Canonical test: {case['name']}"],
        "files": files,
        "initial_prompt": user_prompt or "Relatório exploratório dos dados",
        "current_goal": effective_prompt,
        "is_exploratory": not bool(user_prompt),
        "refinement_history": [],
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
        "settings": settings,
        "script": "",
        "provider": case.get("provider", "deepseek"),
        "model_name": case.get("model_name", "deepseek-v4-flash"),
        "data_filter": {},
        "data_preprocessing": {},
        "pdf_version": 0,
        "retry_count": 0,
        "explore_history": [],
        **paths.session_fields(),
    }, owner_id=owner_id)
    return job_id


def canonical_owner_id(email="canonical-tests@example.com"):
    """Canonical reports belong to a dedicated account without a password."""
    from app.services import auth_service
    user = auth_service.get_user_by_email(email)
    if user is None and email != "canonical-tests@example.com":
        raise RuntimeError("The requested canonical-test owner does not exist.")
    user = user or auth_service.create_user(email, None, display_name="Canonical tests", verified=True)
    if user.status != "active" or user.email_verified_at is None:
        raise RuntimeError("Canonical tests require an active, verified owner.")
    return user.id


def select_cases(playbook, provider=None, model_name=None, case_ids=None):
    """Override a playbook for one provider without changing its source file."""
    if model_name and not provider:
        raise ValueError("--model requires --provider")
    if provider and provider not in ADAPTERS:
        raise ValueError("Unknown provider")
    wanted = set(case_ids or [])
    if wanted - {case["id"] for case in playbook}:
        raise ValueError("Unknown canonical case")
    cases = [dict(case) for case in playbook if not wanted or case["id"] in wanted]
    for case in cases:
        if provider:
            case["provider"] = provider
            case["model_name"] = current_model_name(provider, model_name or DEFAULT_MODEL_BY_PROVIDER[provider])
    return cases


def apply_refinement(job_id: str, prompt: str):
    append_refinement_message(job_id, "user", prompt)
    process_chat_refinement(job_id, prompt)


def validate_session(job_id: str, case: dict, artifact_dir: Path | None = None, save_artifacts: bool = True) -> dict:
    session = get_session(job_id)
    checks = []
    artifacts = {}

    pdf_path = session.get("pdf_path")
    pdf_exists = bool(pdf_path and os.path.exists(pdf_path))
    manifest_path = Path(f"{pdf_path}.manifest.json") if pdf_path else None
    manifest = None
    if manifest_path and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    checks.extend(validate_component_outcomes(session, manifest))
    checks.append(check("pdf_exists", pdf_exists, pdf_path or "missing"))
    if pdf_exists:
        checks.append(check("pdf_size_gt_20kb", os.path.getsize(pdf_path) > 20_000, os.path.getsize(pdf_path)))
        checks.append(check("pdf_pages_match_template", pdf_page_count(pdf_path) >= max_template_page(session), pdf_page_count(pdf_path)))
        if save_artifacts and artifact_dir:
            artifacts = save_case_artifacts(case, job_id, session, Path(pdf_path), artifact_dir)

    checks.append(check("status_completed", "conclu" in session.get("status", "").lower(), session.get("status", "")))
    checks.append(check("has_title", bool(session.get("settings", {}).get("title")), session.get("settings", {}).get("title", "")))
    checks.append(check("has_artifacts", len(session.get("artifacts", [])) >= 3, len(session.get("artifacts", []))))
    checks.append(check("has_template", len(session.get("template", [])) > 0, len(session.get("template", []))))
    checks.append(check("template_in_bounds", template_in_bounds(session.get("template", [])), session.get("template", [])))
    checks.append(check("template_no_overlap", not has_major_overlap(session.get("template", [])), session.get("template", [])))
    checks.append(check("has_non_na_metrics", has_non_na_metrics(session.get("calculated_metrics", {})), session.get("calculated_metrics", {})))
    checks.extend(validate_chart_layout_safety(session.get("components", {})))

    metadata_text = json.dumps([f.get("artifact") for f in session.get("files", [])], ensure_ascii=False).lower()
    for column in case.get("expected", {}).get("columns", []):
        checks.append(check(f"metadata_column_{column}", column.lower() in metadata_text, column))

    pdf_text = artifacts.get("pdf_text", "")
    if pdf_text:
        checks.append(check("pdf_text_min_chars", len(pdf_text.strip()) > 250, len(pdf_text.strip())))
        checks.append(check("pdf_text_no_cut_ellipsis", "..." not in pdf_text, "found ..." if "..." in pdf_text else "ok"))
        title = session.get("settings", {}).get("title", "")
        checks.append(check("pdf_text_title_present", title_in_pdf_text(title, pdf_text), title))
    elif pdf_exists and save_artifacts:
        checks.append(check("pdf_text_extracted", False, "pdftotext unavailable or failed"))

    screenshot_path = artifacts.get("page_1_png_path")
    if screenshot_path:
        screenshot_size = os.path.getsize(screenshot_path)
        checks.append(check("page_1_png_exists", True, screenshot_path))
        checks.append(check("page_1_png_size_gt_10kb", screenshot_size > 10_000, screenshot_size))
    elif pdf_exists and save_artifacts:
        checks.append(check("page_1_png_exists", False, "pdftoppm unavailable or failed"))

    script_text = session.get("script", "").lower()
    searchable_report_text = " ".join([script_text, metadata_text, pdf_text.lower()])
    for keyword in case.get("expected", {}).get("keywords", []):
        checks.append(check(f"report_keyword_{keyword}", keyword.lower() in searchable_report_text, keyword))

    expected = case.get("expected", {})
    if expected.get("filter") or expected.get("preprocessing"):
        checks.extend(validate_expected_preprocessing(session, expected))

    result = {
        "case_id": case["id"],
        "job_id": job_id,
        "pdf_path": pdf_path,
        "artifact_dir": str(artifact_dir) if artifact_dir else None,
        "review_pdf_path": artifacts.get("review_pdf_path"),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "render_summary": summarize_render_manifest(manifest or {}),
        "llm_usage": session.get("llm_usage", {}),
    }
    if save_artifacts and artifact_dir:
        write_json(artifact_dir / "checks.json", result)
    return result


def validate_component_outcomes(session: dict, manifest: dict | None) -> list[dict]:
    expected = {item["id"] for item in session.get("template", [])}
    components = (manifest or {}).get("components", [])
    rendered = {item.get("id") for item in components if item.get("status") == "rendered"}
    invalid = [{"id": item.get("id"), "status": item.get("status")} for item in components if item.get("status") != "rendered"]
    return [
        check("component_manifest_present", manifest is not None, "missing" if manifest is None else "present"),
        check("all_components_rendered", rendered == expected and not invalid, {"missing": sorted(expected - rendered), "invalid": invalid}),
    ]


def check(name: str, passed: bool, detail):
    return {"name": name, "passed": bool(passed), "detail": detail}


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def save_case_artifacts(case: dict, job_id: str, session: dict, pdf_path: Path, artifact_dir: Path) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    GENERATED_PDFS_DIR.mkdir(parents=True, exist_ok=True)

    session_path = artifact_dir / "session.json"
    report_path = artifact_dir / "report.pdf"
    text_path = artifact_dir / "pdftotext.txt"
    screenshot_path = artifact_dir / "page-1.png"
    review_pdf_path = GENERATED_PDFS_DIR / f"{case['id']}__{job_id}.pdf"

    write_json(session_path, session)
    shutil.copy2(pdf_path, report_path)
    shutil.copy2(pdf_path, review_pdf_path)

    pdf_text = extract_pdf_text(report_path, text_path)
    rendered_png = render_first_page_png(report_path, screenshot_path)

    return {
        "session_path": str(session_path),
        "report_pdf_path": str(report_path),
        "review_pdf_path": str(review_pdf_path),
        "pdftotext_path": str(text_path) if text_path.exists() else None,
        "page_1_png_path": str(rendered_png) if rendered_png else None,
        "pdf_text": pdf_text,
    }


def extract_pdf_text(pdf_path: Path, output_path: Path) -> str:
    if not shutil.which("pdftotext"):
        return ""
    result = subprocess.run(
        ["pdftotext", str(pdf_path), str(output_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists():
        return ""
    return output_path.read_text(encoding="utf-8", errors="replace")


def render_first_page_png(pdf_path: Path, output_path: Path) -> Path | None:
    if not shutil.which("pdftoppm"):
        return None
    prefix = output_path.with_suffix("")
    result = subprocess.run(
        ["pdftoppm", "-png", "-f", "1", "-singlefile", str(pdf_path), str(prefix)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return output_path if result.returncode == 0 and output_path.exists() else None


def title_in_pdf_text(title: str, pdf_text: str) -> bool:
    title_words = [word.lower() for word in title.split() if len(word) > 3]
    if not title_words:
        return False
    pdf_text = pdf_text.lower()
    matched = sum(1 for word in title_words[:8] if word in pdf_text)
    return matched >= max(1, min(3, len(title_words)))


def validate_expected_filter(session: dict) -> list[dict]:
    return validate_expected_preprocessing(session, {"filter": True})


def validate_expected_preprocessing(session: dict, expected: dict | None = None) -> list[dict]:
    expected = expected or {}
    checks = []
    files = session.get("files", [])
    data_filter = session.get("data_preprocessing") or session.get("data_filter", {})
    filtered_files = [file_info for file_info in files if file_info.get("preprocessed_path") or file_info.get("filtered_path")]

    checks.append(check("data_preprocessing_needed", (data_filter.get("needs_preprocessing") or data_filter.get("needs_filter")) is True, data_filter))
    checks.append(check("data_preprocessing_has_output_path", bool(filtered_files), [f.get("preprocessed_path") or f.get("filtered_path") for f in files]))
    processed_paths = [str(file_info.get("preprocessed_path") or file_info["filtered_path"]) for file_info in filtered_files]
    script = session.get("script", "")
    checks.append(check("script_uses_filtered_file", bool(processed_paths) and all(
        path in script or str(Path(path).resolve()) in script for path in processed_paths
    ), processed_paths))

    if expected.get("filter"):
        row_changes = []
        for file_info in filtered_files:
            raw_rows = (file_info.get("raw_artifact") or {}).get("shape", {}).get("rows")
            filtered_rows = (file_info.get("artifact") or {}).get("shape", {}).get("rows")
            if raw_rows is not None and filtered_rows is not None:
                row_changes.append({"file": file_info.get("original_filename"), "raw_rows": raw_rows, "filtered_rows": filtered_rows})
        checks.append(check(
            "data_filter_changed_row_count",
            any(item["filtered_rows"] < item["raw_rows"] for item in row_changes),
            row_changes,
        ))

    computed_columns = expected.get("computed_columns", [])
    if computed_columns:
        processed_metadata = json.dumps([file_info.get("artifact") for file_info in filtered_files], ensure_ascii=False).lower()
        for column in computed_columns:
            checks.append(check(
                f"data_preprocessing_computed_column_{column}",
                column.lower() in processed_metadata,
                column,
            ))
    return checks


def validate_chart_layout_safety(components: dict) -> list[dict]:
    risky_components = []
    for component_id, code in components.items():
        lowered = str(code).lower()
        has_x_label = bool(re.search(r"\.set_xlabel\(\s*['\"][^'\"]+['\"]", lowered))
        has_bottom_legend = bool(re.search(r"bbox_to_anchor\s*=\s*\([^)]*-\d*\.?\d+", lowered))
        if has_x_label and has_bottom_legend:
            risky_components.append({
                "id": component_id,
                "reason": "x_label_with_bottom_legend",
            })

        has_data_annotation = any(token in lowered for token in [
            "ax.annotate",
            "plt.annotate",
            "ax.text(",
            "plt.text(",
        ])
        if not has_data_annotation:
            continue
        has_inside_upper_legend = (
            "legend(loc='upper right'" in lowered
            or 'legend(loc="upper right"' in lowered
            or "legend(loc='upper left'" in lowered
            or 'legend(loc="upper left"' in lowered
            or "legend(loc='best'" in lowered
            or 'legend(loc="best"' in lowered
        )
        has_outside_anchor = "bbox_to_anchor" in lowered
        has_headroom = "set_ylim" in lowered
        if has_inside_upper_legend and not has_outside_anchor:
            risky_components.append({
                "id": component_id,
                "reason": "data_label_with_inside_upper_legend",
            })
        elif has_inside_upper_legend and not has_headroom:
            risky_components.append({
                "id": component_id,
                "reason": "data_label_with_legend_without_y_headroom",
            })

    return [check("chart_annotations_do_not_overlap_legends", not risky_components, risky_components)]


def pdf_page_count(pdf_path: str) -> int:
    with open(pdf_path, "rb") as f:
        content = f.read()
    return len([m for m in content.split(b"/Type /Page")]) - 1


def max_template_page(session: dict) -> int:
    return max([item.get("page", 1) for item in session.get("template", [])] or [1])


def template_in_bounds(template: list[dict]) -> bool:
    for item in template:
        if item.get("x", 0) < 0 or item.get("y", 0) < 0:
            return False
        if item.get("x", 0) + item.get("w", 0) > 210:
            return False
        if item.get("y", 0) + item.get("h", 0) > 297:
            return False
    return True


def has_major_overlap(template: list[dict]) -> bool:
    visible = [item for item in template if item.get("w", 0) > 0 and item.get("h", 0) > 0]
    for index, first in enumerate(visible):
        for second in visible[index + 1:]:
            if first.get("page", 1) != second.get("page", 1):
                continue
            overlap = overlap_area(first, second)
            smaller = min(first["w"] * first["h"], second["w"] * second["h"])
            if smaller and overlap / smaller > 0.08:
                return True
    return False


def overlap_area(first: dict, second: dict) -> float:
    left = max(first["x"], second["x"])
    right = min(first["x"] + first["w"], second["x"] + second["w"])
    top = max(first["y"], second["y"])
    bottom = min(first["y"] + first["h"], second["y"] + second["h"])
    return max(0, right - left) * max(0, bottom - top)


def has_non_na_metrics(metrics: dict) -> bool:
    return any(value not in (None, "", "N/A") and "Error" not in str(value) for value in metrics.values())


def run_case(case: dict, apply_refinements: bool, run_dir: Path, save_artifacts: bool, owner_id=None) -> dict:
    job_id = create_canonical_session(case, owner_id=owner_id)
    run_pipeline(job_id)
    if apply_refinements:
        for prompt in case.get("refinement_prompts", []):
            apply_refinement(job_id, prompt)
            time.sleep(1)
    case_dir = run_dir / case["id"] if save_artifacts else None
    return validate_session(job_id, case, case_dir, save_artifacts=save_artifacts)


def main():
    parser = argparse.ArgumentParser(description="Run canonical report-generation sessions for any configured provider.")
    parser.add_argument("--provider", choices=ADAPTERS, help="Override the provider for every selected case.")
    parser.add_argument("--model", help="Override the model (requires --provider).")
    parser.add_argument("--owner-email", default="canonical-tests@example.com", help="Account whose provider keys the tests use.")
    parser.add_argument("--env-file", type=Path, help="Load a private environment file, without printing its contents.")
    parser.add_argument("--case", action="append", help="Case id to run. Can be passed multiple times.")
    parser.add_argument("--with-refinements", action="store_true", help="Apply refinement prompts after first generation.")
    parser.add_argument("--no-save-artifacts", action="store_true", help="Skip PDF/text/screenshot artifact capture.")
    parser.add_argument("--dry-run", action="store_true", help="Only print the playbook without LLM calls.")
    args = parser.parse_args()

    load_dotenv(args.env_file, override=True) if args.env_file else load_dotenv()
    install_log_redaction()
    cases = select_cases(load_playbook(), args.provider, args.model, args.case)

    if args.dry_run:
        print(json.dumps(cases, indent=2, ensure_ascii=False))
        return

    dev_server_keys_enabled()  # rejects production use of the dev escape hatch
    owner_id = canonical_owner_id(args.owner_email)
    for provider in {case.get("provider", "deepseek") for case in cases}:
        with llm_operation_context(user_id=owner_id):
            resolve_api_key(owner_id, provider)
    ensure_fixtures()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{int(time.time())}-{args.provider or 'mixed'}-{uuid.uuid4().hex[:6]}"
    run_dir = RESULTS_DIR / run_id
    if not args.no_save_artifacts:
        run_dir.mkdir(parents=True, exist_ok=True)
    results = [run_case(case, apply_refinements=args.with_refinements, run_dir=run_dir, save_artifacts=not args.no_save_artifacts, owner_id=owner_id) for case in cases]
    output_path = run_dir / "canonical_results.json" if not args.no_save_artifacts else RESULTS_DIR / f"canonical_results_{run_id}.json"
    write_json(output_path, results)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Saved results to {output_path}")
    if not args.no_save_artifacts:
        print(f"Saved review PDFs to {GENERATED_PDFS_DIR}")

    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
