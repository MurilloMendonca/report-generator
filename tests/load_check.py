"""Deterministic capacity check with real jobs, database writes and sandboxed PDFs.

On the Mac host: sh scripts/load-check.sh 10 (isolates and cleans up Docker data).
Direct execution needs a disposable test DB/workspace:
APP_ENV=test MPLBACKEND=Agg python -m tests.load_check --users 10
No live provider calls; use canonical sessions separately for provider latency.
"""
import argparse
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete

from app import db
from app.models.db import User
from app.services import auth_service, job_runner, session_manager
from app.services.cleanup_service import delete_entire_session
from app.services.executor_service import execute_script
from app.utils.report_paths import ReportPaths


def main():
    import os
    if os.getenv("APP_ENV") != "test":
        raise SystemExit("Run only with APP_ENV=test and a disposable _test database.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, choices=range(5, 11), default=10)
    args = parser.parse_args()
    barrier = threading.Barrier(args.users)
    active = peak = 0
    guard = threading.Lock()
    latencies = []
    users, reports = [], []
    try:
        for _ in range(args.users):
            user = auth_service.create_user(f"load-{uuid.uuid4().hex}@example.com", None, verified=True)
            users.append(user.id)
            job = f"test-load-{uuid.uuid4().hex}"
            paths = ReportPaths.for_new_report(job, str(user.id)).ensure()
            session_manager.create_session(job, {"job_id": job, "current_step": "completed", **paths.session_fields()}, owner_id=user.id)
            reports.append((job, paths))
        def render(job, paths):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            try:
                output = paths.output / "load.pdf"
                result = execute_script("from fpdf import FPDF\npdf=FPDF()\npdf.add_page()\npdf.set_font('Helvetica', size=12)\npdf.cell(0, 10, 'Capacity check')\npdf.output(" + repr(str(output)) + ")", str(paths.scripts / "load.py"), paths=paths)
                if not result["success"] or not output.read_bytes().startswith(b"%PDF-"):
                    raise RuntimeError("Sandboxed PDF generation failed")
                session_manager.update_session(job, {"pdf_path": str(output), "current_step": "completed"})
                return True
            finally:
                with guard:
                    active -= 1
        def run(report):
            barrier.wait(timeout=30)
            start = time.perf_counter()
            result = job_runner.run_job(render, report[0], report[1])
            latencies.append(time.perf_counter() - start)
            return result
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.users) as pool:
            results = list(pool.map(run, reports))
        result = {"users": args.users, "successes": sum(value is True for value in results), "peak_jobs": peak,
                  "configured_slots": job_runner.MAX_CONCURRENT_JOBS, "elapsed_seconds": round(time.perf_counter() - start, 2),
                  "max_user_seconds": round(max(latencies), 2), "mean_user_seconds": round(sum(latencies) / len(latencies), 2)}
        result["passed"] = all(value is True for value in results) and peak <= job_runner.MAX_CONCURRENT_JOBS
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    finally:
        for job, _ in reports:
            delete_entire_session(job)
        with db.transaction() as tx:
            tx.execute(delete(User).where(User.id.in_(users)))


if __name__ == "__main__":
    raise SystemExit(main())
