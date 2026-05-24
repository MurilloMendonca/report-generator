import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from app.main import DEFAULT_SETTINGS, run_pipeline, run_script_generation
from app.services.llm_service import refine_session_modular
from app.services.session_manager import add_log, create_session, get_session, update_session


CANONICAL_DIR = Path(__file__).parent
UPLOAD_DIR = ROOT / "workspace" / "uploads"
RESULTS_DIR = CANONICAL_DIR / "results"


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


def create_canonical_session(case: dict) -> str:
    job_id = f"canonical-{case['id']}-{uuid.uuid4().hex[:8]}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    files = []
    for relative_path in case["files"]:
        source = CANONICAL_DIR / relative_path
        suffix = source.suffix
        target = UPLOAD_DIR / f"{uuid.uuid4()}{suffix}"
        shutil.copy2(source, target)
        files.append({
            "original_filename": source.name,
            "path": str(target.relative_to(ROOT)),
            "artifact": None,
        })

    settings = DEFAULT_SETTINGS.copy()
    settings["selected_template_id"] = case["template_id"]

    create_session(job_id, {
        "job_id": job_id,
        "status": "Iniciando teste canônico...",
        "logs": [f"Canonical test: {case['name']}"],
        "files": files,
        "initial_prompt": case["initial_prompt"],
        "current_goal": case["initial_prompt"],
        "refinement_history": [],
        "artifacts": [],
        "template": [],
        "components": {},
        "calculated_metrics": {},
        "settings": settings,
        "script": "",
        "provider": case.get("provider", "deepseek"),
        "model_name": case.get("model_name", "deepseek-chat"),
        "pdf_version": 0,
        "retry_count": 0,
        "explore_history": [],
    })
    return job_id


def apply_refinement(job_id: str, prompt: str):
    session = get_session(job_id)
    history = session.get("refinement_history", [])
    history.append(prompt)
    full_goal = session["initial_prompt"] + "\nRefinement History:\n- " + "\n- ".join(history)

    add_log(job_id, f"Canonical refinement: {prompt}")
    new_state = refine_session_modular(session, prompt)
    new_state.update({"refinement_history": history, "current_goal": full_goal})

    if new_state.get("needs_full_regeneration"):
        update_session(job_id, {
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
            "refinement_history": history,
            "current_goal": full_goal,
        })
        run_pipeline(job_id)
    else:
        update_session(job_id, new_state)
        if new_state.get("needs_script_update"):
            run_script_generation(job_id)


def validate_session(job_id: str, case: dict) -> dict:
    session = get_session(job_id)
    checks = []

    pdf_path = session.get("pdf_path")
    pdf_exists = bool(pdf_path and os.path.exists(pdf_path))
    checks.append(check("pdf_exists", pdf_exists, pdf_path or "missing"))
    if pdf_exists:
        checks.append(check("pdf_size_gt_20kb", os.path.getsize(pdf_path) > 20_000, os.path.getsize(pdf_path)))
        checks.append(check("pdf_pages_match_template", pdf_page_count(pdf_path) >= max_template_page(session), pdf_page_count(pdf_path)))

    checks.append(check("status_completed", "conclu" in session.get("status", "").lower(), session.get("status", "")))
    checks.append(check("has_title", bool(session.get("settings", {}).get("title")), session.get("settings", {}).get("title", "")))
    checks.append(check("has_artifacts", len(session.get("artifacts", [])) >= 3, len(session.get("artifacts", []))))
    checks.append(check("has_template", len(session.get("template", [])) > 0, len(session.get("template", []))))
    checks.append(check("template_in_bounds", template_in_bounds(session.get("template", [])), session.get("template", [])))
    checks.append(check("template_no_overlap", not has_major_overlap(session.get("template", [])), session.get("template", [])))
    checks.append(check("has_non_na_metrics", has_non_na_metrics(session.get("calculated_metrics", {})), session.get("calculated_metrics", {})))

    metadata_text = json.dumps([f.get("artifact") for f in session.get("files", [])], ensure_ascii=False).lower()
    for column in case.get("expected", {}).get("columns", []):
        checks.append(check(f"metadata_column_{column}", column.lower() in metadata_text, column))

    script_text = session.get("script", "").lower()
    for keyword in case.get("expected", {}).get("keywords", []):
        checks.append(check(f"report_keyword_{keyword}", keyword.lower() in script_text or keyword.lower() in metadata_text, keyword))

    return {
        "case_id": case["id"],
        "job_id": job_id,
        "pdf_path": pdf_path,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }


def check(name: str, passed: bool, detail):
    return {"name": name, "passed": bool(passed), "detail": detail}


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


def run_case(case: dict, apply_refinements: bool) -> dict:
    job_id = create_canonical_session(case)
    run_pipeline(job_id)
    if apply_refinements:
        for prompt in case.get("refinement_prompts", []):
            apply_refinement(job_id, prompt)
            time.sleep(1)
    return validate_session(job_id, case)


def main():
    parser = argparse.ArgumentParser(description="Run canonical DeepSeek report-generation sessions.")
    parser.add_argument("--case", action="append", help="Case id to run. Can be passed multiple times.")
    parser.add_argument("--with-refinements", action="store_true", help="Apply refinement prompts after first generation.")
    parser.add_argument("--dry-run", action="store_true", help="Only print the playbook without LLM calls.")
    args = parser.parse_args()

    load_dotenv()
    ensure_fixtures()
    cases = load_playbook()
    if args.case:
        wanted = set(args.case)
        cases = [case for case in cases if case["id"] in wanted]

    if args.dry_run:
        print(json.dumps(cases, indent=2, ensure_ascii=False))
        return

    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required for canonical sessions.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = [run_case(case, apply_refinements=args.with_refinements) for case in cases]
    output_path = RESULTS_DIR / f"canonical_results_{int(time.time())}.json"
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Saved results to {output_path}")

    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
