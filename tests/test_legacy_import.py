import io
import json
import shutil
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import delete, select

from app import db
from app.cli import main as cli_main
from app.models.db import LayoutTemplate, Report, User
from app.services.legacy_import import ensure_demo_user, import_legacy
from app.services.pipeline_service import run_script_generation, text_component_code
from app.services.session_manager import get_session
from app.utils.report_paths import TENANTS_DIR, ReportPaths


def legacy_render_session(job_id: str, workspace: Path) -> dict:
    """A finished report in the old shared layout, renderable without model calls."""
    upload = workspace / "uploads" / f"{uuid.uuid4().hex}.csv"
    upload.write_text("region,revenue\nNorth,10\nSouth,20\n")
    text = {"id": "t_summary", "type": "text", "indicator": "Summary", "columns": []}
    chart = {
        "id": "g_sales", "type": "graphic", "indicator": "Revenue by region",
        "columns": ["sales.csv:region", "sales.csv:revenue"],
        "component_preset_id": "bar_ranked", "render_mode": "library",
        "data_binding": {"source": "sales.csv", "roles": {"category": "region", "value": "revenue"}, "aggregation": "sum", "filters": []},
    }
    pdf = workspace / "reports" / f"{job_id}.pdf"
    pdf.write_bytes(b"%PDF-old")
    (workspace / "reports" / f"{job_id}.pdf.manifest.json").write_text("{}")
    script = workspace / "scripts" / f"{job_id}.py"
    script.write_text(f"print({str(upload)!r})\n")
    charts = workspace / "reports" / f"explore_{job_id}_turn1"
    charts.mkdir()
    (charts / "chart_1.png").write_bytes(b"png")
    return {
        "job_id": job_id, "initial_prompt": "Analyze revenue", "settings": {"title": "Sales report"},
        "current_step": "completed", "pdf_version": 1, "status": "Concluído",
        "created_at": "2025-01-02T03:04:05+00:00", "updated_at": "2025-01-02T03:04:06+00:00",
        "files": [{"original_filename": "sales.csv", "path": str(upload), "artifact": {
            "filename": "sales.csv", "columns": [
                {"name": "region", "type": "object", "role": "dimension"},
                {"name": "revenue", "type": "int64", "role": "measure"},
            ],
        }}],
        "artifacts": [text, chart], "components": {"t_summary": text_component_code(text, "Revenue totals 30.")},
        "template": [
            {"id": "t_summary", "page": 1, "x": 10, "y": 30, "w": 190, "h": 30},
            {"id": "g_sales", "page": 1, "x": 10, "y": 70, "w": 190, "h": 100},
        ],
        "script": script.read_text(), "script_path": str(script), "pdf_path": str(pdf),
    }


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="legacy-workspace-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)
        for name in ("sessions", "uploads", "filtered", "scripts", "reports"):
            (self.workspace / name).mkdir()
        self.email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
        self.owner, created = ensure_demo_user(self.email.upper())
        self.assertTrue(created)
        self.addCleanup(self.remove_owner)

        self.legacy_id = f"test-legacy-{uuid.uuid4().hex}"
        self.legacy = legacy_render_session(self.legacy_id, self.workspace)
        self.write_session(self.legacy_id, self.legacy)

        # A report already in the Phase 0 per-report layout.
        self.phase0_id = f"test-legacy-{uuid.uuid4().hex}"
        phase0 = ReportPaths.for_new_report(self.phase0_id).ensure()
        self.addCleanup(shutil.rmtree, phase0.root, True)
        (phase0.uploads / "data.csv").write_text("a\n1\n")
        self.write_session(self.phase0_id, {
            "job_id": self.phase0_id, "storage_dir": str(phase0.root),
            "files": [{"original_filename": "data.csv", "path": str(phase0.uploads / "data.csv")}],
        })

        (self.workspace / "sessions" / "broken.json").write_text("{not json")
        (self.workspace / "layout_templates.json").write_text(json.dumps([
            {"id": "executive_standard", "name": "Edited built-in", "description": "", "slots": []},
            {"id": f"tpl_{uuid.uuid4().hex[:8]}", "name": "Custom", "description": "", "slots": []},
        ]))

    def write_session(self, job_id, state):
        (self.workspace / "sessions" / f"{job_id}.json").write_text(json.dumps(state))

    def remove_owner(self):
        shutil.rmtree(TENANTS_DIR / str(self.owner.id), ignore_errors=True)
        with db.transaction() as tx:
            tx.execute(delete(LayoutTemplate).where(LayoutTemplate.id == "executive_standard"))
            tx.execute(delete(User).where(User.id == self.owner.id))  # cascades to reports and templates

    def test_dry_run_changes_nothing(self):
        summary = import_legacy(self.email, workspace=self.workspace, dry_run=True)
        self.assertEqual(sorted(summary.imported), sorted([self.legacy_id, self.phase0_id]))
        self.assertEqual(summary.unreadable, ["broken.json"])
        self.assertEqual(summary.templates_imported, 2)
        self.assertIsNone(get_session(self.legacy_id))
        self.assertFalse((TENANTS_DIR / str(self.owner.id)).exists())

    def test_import_moves_reports_to_the_owner_and_they_still_render(self):
        summary = import_legacy(self.email, workspace=self.workspace)
        self.assertEqual(summary.missing_files, {})
        with db.transaction() as tx:
            reports = {r.id: r for r in tx.execute(select(Report).where(Report.owner_id == self.owner.id)).scalars()}
        self.assertEqual(set(reports), {self.legacy_id, self.phase0_id})
        self.assertEqual(reports[self.legacy_id].title, "Sales report")
        self.assertEqual(reports[self.legacy_id].created_at.year, 2025)
        self.assertGreater(reports[self.legacy_id].storage_bytes, 0)

        session = get_session(self.legacy_id)
        paths = ReportPaths.from_session(session)
        self.assertEqual(paths.root, TENANTS_DIR / str(self.owner.id) / "reports" / self.legacy_id)
        upload = Path(session["files"][0]["path"])
        self.assertEqual(upload.parent, paths.uploads)
        self.assertEqual(Path(session["pdf_path"]), paths.output / f"{self.legacy_id}.pdf")
        self.assertEqual(Path(session["script_path"]), paths.scripts / f"{self.legacy_id}.py")
        self.assertIn(str(upload), session["script"])  # paths inside generated code too
        self.assertNotIn(str(self.workspace), json.dumps(session))
        self.assertTrue((paths.output / f"explore_{self.legacy_id}_turn1" / "chart_1.png").exists())
        self.assertTrue((paths.output / f"{self.legacy_id}.pdf.manifest.json").exists())
        # The old files stay until the operator removes them.
        self.assertTrue(Path(self.legacy["pdf_path"]).exists())

        phase0 = get_session(self.phase0_id)
        self.assertTrue(Path(phase0["files"][0]["path"]).exists())
        self.assertEqual(ReportPaths.from_session(phase0).root.parent.parent.name, str(self.owner.id))

        templates = {row.id: row for row in db.new_session().execute(select(LayoutTemplate)).scalars()}
        custom = [row for row in templates.values() if row.data.get("name") == "Custom"]
        self.assertEqual(len(custom), 1)
        self.assertEqual(custom[0].owner_id, self.owner.id)
        self.assertEqual(templates["executive_standard"].data["name"], "Edited built-in")

        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("no model calls")):
            run_script_generation(self.legacy_id)
        rendered = get_session(self.legacy_id)
        self.assertEqual(rendered["current_step"], "completed", rendered.get("error"))
        self.assertTrue(Path(rendered["pdf_path"]).read_bytes().startswith(b"%PDF-1"))
        self.assertEqual(Path(rendered["pdf_path"]).parent.resolve(), paths.output.resolve())

        again = import_legacy(self.email, workspace=self.workspace)
        self.assertEqual(again.imported, [])
        self.assertEqual(sorted(again.already_imported), sorted([self.legacy_id, self.phase0_id]))
        self.assertEqual(again.templates_imported, 0)

    def test_reports_files_that_are_gone(self):
        Path(self.legacy["pdf_path"]).unlink()
        summary = import_legacy(self.email, workspace=self.workspace, dry_run=True)
        self.assertEqual(summary.missing_files, {self.legacy_id: [self.legacy["pdf_path"]]})

    def test_cli_requires_an_existing_owner(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["import-legacy", "--owner", "nobody@example.com", "--workspace", str(self.workspace)]), 1)
            self.assertEqual(cli_main(["import-legacy", "--owner", self.email, "--workspace", str(self.workspace), "--dry-run"]), 0)
        self.assertIsNone(get_session(self.legacy_id))

    def test_demo_user_is_created_once(self):
        user, created = ensure_demo_user(self.email)
        self.assertFalse(created)
        self.assertEqual(user.id, self.owner.id)
        self.assertTrue(user.is_demo)
        self.assertIsNotNone(user.email_verified_at)
        self.assertEqual(user.email, self.email.lower())


if __name__ == "__main__":
    unittest.main()
