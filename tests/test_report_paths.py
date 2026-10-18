import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.routers.api as api
from app.main import app
from tests.auth_helpers import signed_in_headers
from app.services.cleanup_service import delete_entire_session, get_session_generated_paths, safe_remove_path
from app.services.executor_service import execute_script, probe_bubblewrap
from app.services.session_manager import delete_session, get_session
from app.utils.report_paths import TENANTS_DIR, ReportPaths
from tests.auth_helpers import create_session

BWRAP_AVAILABLE, BWRAP_ERROR = probe_bubblewrap()


def new_report(test: unittest.TestCase, tenant: str = "default") -> ReportPaths:
    job_id = f"test-paths-{uuid.uuid4().hex}"
    paths = ReportPaths.for_new_report(job_id, tenant).ensure()
    test.addCleanup(shutil.rmtree, paths.root, True)
    return paths


class ReportPathsTests(unittest.TestCase):
    def test_new_report_layout(self):
        paths = ReportPaths.for_new_report("abc-123")
        self.assertEqual(paths.root, TENANTS_DIR / "default" / "reports" / "abc-123")
        self.assertEqual(paths.uploads, paths.root / "uploads")
        self.assertEqual(paths.sandbox_read_only, [paths.uploads])
        self.assertEqual(paths.sandbox_writable, [paths.filtered, paths.scripts, paths.output])
        self.assertEqual(paths.session_fields(), {"storage_dir": str(paths.root)})

    def test_rejects_unsafe_identifiers(self):
        for job_id, tenant in (("../x", "default"), ("ok", "../x"), ("a/b", "default"), ("", "default")):
            with self.assertRaises(ValueError):
                ReportPaths.for_new_report(job_id, tenant)

    def test_legacy_session_uses_shared_folders(self):
        paths = ReportPaths.from_session({"job_id": "old"})
        self.assertTrue(paths.is_legacy)
        self.assertEqual(paths.output, Path("workspace/reports"))
        self.assertEqual(paths.session_fields(), {})

    def test_storage_dir_outside_tenants_is_rejected(self):
        for storage_dir in ("workspace/sessions/x", "/etc", str(TENANTS_DIR / "default" / "reports" / "other")):
            with self.assertRaises(ValueError):
                ReportPaths.from_session({"job_id": "x", "storage_dir": storage_dir})


class SandboxPerReportTests(unittest.TestCase):
    """A report's script sees only that report's folders."""

    def setUp(self):
        self.a = new_report(self)
        self.b = new_report(self)
        (self.a.uploads / "mine.csv").write_text("x\n1\n")
        (self.b.uploads / "secret.csv").write_text("x\n42\n")
        (self.b.output / "report.pdf").write_text("%PDF-b")

    def run_in(self, paths, code):
        return execute_script(code, str(paths.scripts / f"probe_{uuid.uuid4().hex}.py"), paths=paths)

    def test_guard_denies_other_reports_files(self):
        result = self.run_in(self.a, f"open({str((self.b.uploads / 'secret.csv').resolve())!r}).read()")
        self.assertFalse(result["success"])
        self.assertIn("Sandbox denied filesystem access", result["stderr"])

    def test_own_files_are_readable_and_outputs_writable(self):
        target = self.a.output / "out.txt"
        result = self.run_in(self.a, "\n".join([
            f"print(open({str((self.a.uploads / 'mine.csv').resolve())!r}).read().strip())",
            f"open({str(target.resolve())!r}, 'w').write('ok')",
        ]))
        self.assertTrue(result["success"], result["stderr"])
        self.assertEqual(result["stdout"].strip(), "x\n1")
        self.assertEqual(target.read_text(), "ok")

    def test_generated_symlinks_and_special_files_are_rejected(self):
        for name, code in [("link", f"import os\nos.symlink({str((self.b.uploads / 'secret.csv').resolve())!r}, {str(self.a.filtered / 'link')!r})"),
                           ("pipe", f"import os\nos.mkfifo({str(self.a.filtered / 'pipe')!r})")]:
            result = self.run_in(self.a, code)
            self.assertFalse(result["success"], result)
            self.assertIn("output rejected", result["stderr"])
            self.assertFalse(os.path.lexists(self.a.filtered / name))
        self.assertEqual((self.b.uploads / "secret.csv").read_text(), "x\n42\n")

    def test_cleanup_cannot_follow_a_linked_parent(self):
        link = self.a.output / "linked"
        link.symlink_to(self.b.output.resolve(), target_is_directory=True)
        self.assertFalse(safe_remove_path(str(link / "report.pdf")))
        self.assertTrue((self.b.output / "report.pdf").exists())
        self.assertTrue(safe_remove_path(str(link)))
        self.assertFalse(link.is_symlink())

    @unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
    def test_bubblewrap_hides_other_reports_even_without_the_guard(self):
        # _io.FileIO and os.listdir skip the Python-level guard on purpose.
        probes = {
            "other upload": f"import _io\n_io.FileIO({str((self.b.uploads / 'secret.csv').resolve())!r}).read()",
            "other output": f"import _io\n_io.FileIO({str((self.b.output / 'report.pdf').resolve())!r}, 'w').write(b'x')",
            "tenant listing": f"import os\nprint(os.listdir({str((TENANTS_DIR / 'default' / 'reports').resolve())!r}))",
            "legacy uploads": f"import os\nprint(os.listdir({str(Path('workspace/uploads').resolve())!r}))",
        }
        for name, code in probes.items():
            result = self.run_in(self.a, code)
            if name == "tenant listing" and result["success"]:
                # The parent folder exists inside the sandbox but holds only this report.
                self.assertEqual(eval(result["stdout"]), [self.a.job_id], name)
                continue
            self.assertFalse(result["success"], name)
            self.assertIn("FileNotFoundError", result["stderr"], name)
        self.assertEqual((self.b.output / "report.pdf").read_text(), "%PDF-b")

    @unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
    def test_bubblewrap_keeps_own_uploads_read_only(self):
        result = self.run_in(self.a, f"import _io\n_io.FileIO({str((self.a.uploads / 'mine.csv').resolve())!r}, 'w').write(b'x')")
        self.assertFalse(result["success"])
        self.assertIn("Read-only file system", result["stderr"])
        self.assertEqual((self.a.uploads / "mine.csv").read_text(), "x\n1\n")


class FileServingSecurityTests(unittest.TestCase):
    def setUp(self):
        from tests.auth_helpers import make_user
        from tests.test_isolation import owned_report
        self.owner = make_user()
        self.job, self.paths = owned_report(self.owner)
        self.other_job, self.other_paths = owned_report(make_user())
        self.addCleanup(delete_entire_session, self.job)
        self.addCleanup(delete_entire_session, self.other_job)

    def test_pdf_and_preprocessed_download_reject_links_to_other_reports(self):
        from app.services.session_manager import update_session
        derived = self.paths.filtered / "derived.csv"
        derived.write_text("value\n42\n")
        state = get_session(self.job)
        state["files"][0]["preprocessed_path"] = str(derived)
        update_session(self.job, {"files": state["files"]})
        with TestClient(app) as client:
            for action, disposition in [("preview", "inline"), ("download", "attachment")]:
                response = client.get(f"/api/sessions/{self.job}/files/0/{action}?variant=preprocessed", headers=signed_in_headers(self.owner))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"value\n42\n")
                self.assertIn(disposition, response.headers["content-disposition"])
            pdf = self.paths.output / f"{self.job}.pdf"
            pdf.unlink()
            pdf.symlink_to((self.other_paths.output / f"{self.other_job}.pdf").resolve())
            derived.unlink()
            derived.symlink_to((self.other_paths.uploads / "data.csv").resolve())
            for url in [f"/api/sessions/{self.job}/pdf", f"/api/sessions/{self.job}/files/0/download?variant=preprocessed"]:
                self.assertEqual(client.get(url, headers=signed_in_headers(self.owner)).status_code, 404)

    def test_open_descriptor_survives_path_replacement(self):
        import asyncio
        from app.services.report_files import report_file_response
        pdf = self.paths.output / f"{self.job}.pdf"
        response = report_file_response(get_session(self.job), pdf, media_type="application/pdf")
        pdf.unlink()
        pdf.symlink_to((self.other_paths.uploads / "data.csv").resolve())
        async def consume():
            return b"".join([chunk async for chunk in response.body_iterator])
        self.assertEqual(asyncio.run(consume()), b"%PDF-1.4 secret")

    def test_cannot_serve_a_direct_path_from_another_report(self):
        from fastapi import HTTPException
        from app.services.report_files import report_file_response
        with self.assertRaises(HTTPException) as error:
            report_file_response(get_session(self.job), self.other_paths.uploads / "data.csv", media_type="text/csv")
        self.assertEqual(error.exception.status_code, 404)


class NewSessionStorageTests(unittest.TestCase):
    def setUp(self):
        self.headers = signed_in_headers()

    def test_new_session_stores_uploads_in_its_own_folder_and_delete_removes_it(self):
        with patch.object(api, "run_pipeline"), TestClient(app) as client:
            response = client.post(
                "/api/sessions", headers=self.headers, data={"provider": "deepseek"},
                files={"files": ("data.csv", b"a,b\n1,2\n", "text/csv")},
            )
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()["job_id"]
            self.addCleanup(delete_entire_session, job_id)
            session = get_session(job_id)
            paths = ReportPaths.from_session(session)
            self.assertFalse(paths.is_legacy)
            upload = Path(session["files"][0]["path"])
            self.assertEqual(upload.parent.resolve(), paths.uploads.resolve())
            self.assertTrue(upload.exists())
            self.assertIn(str(paths.root), get_session_generated_paths(job_id, session))

            self.assertEqual(client.delete(f"/api/sessions/{job_id}", headers=self.headers).status_code, 200)
        self.assertFalse(paths.root.exists())

    def test_added_files_go_to_the_same_folder(self):
        paths = new_report(self)
        job_id = paths.job_id
        create_session(job_id, {"job_id": job_id, "files": [], **paths.session_fields()})
        self.addCleanup(delete_entire_session, job_id)
        with patch.object(api, "run_pipeline"), TestClient(app) as client:
            response = client.post(
                f"/api/sessions/{job_id}/files", headers=self.headers,
                files={"files": ("more.csv", b"a\n1\n", "text/csv")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        added = Path(get_session(job_id)["files"][0]["path"])
        self.assertEqual(added.parent.resolve(), paths.uploads.resolve())

    def test_tampered_storage_dir_cannot_point_outside_tenants(self):
        job_id = f"test-paths-{uuid.uuid4().hex}"
        create_session(job_id, {"job_id": job_id, "files": [], "storage_dir": "workspace/sessions"})
        self.addCleanup(delete_session, job_id)
        with patch.object(api, "run_pipeline"), TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/sessions/{job_id}/files", headers=self.headers,
                files={"files": ("more.csv", b"a\n1\n", "text/csv")},
            )
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(any(Path("workspace/sessions").glob("*.csv")))
        self.assertEqual(get_session(job_id)["files"], [])


class PerReportRenderTests(unittest.TestCase):
    """A whole report renders inside its own folder, with no model calls."""

    def test_full_render_and_component_preview_stay_in_the_report_folder(self):
        from app.services.pipeline_service import run_script_generation, text_component_code
        paths = new_report(self)
        job_id = paths.job_id
        self.addCleanup(delete_entire_session, job_id)
        upload = paths.uploads / "sales.csv"
        upload.write_text("region,revenue\nNorth,10\nSouth,20\n")
        text = {"id": "t_summary", "type": "text", "indicator": "Summary", "columns": []}
        chart = {
            "id": "g_sales", "type": "graphic", "indicator": "Revenue by region",
            "columns": ["sales.csv:region", "sales.csv:revenue"],
            "component_preset_id": "bar_ranked", "render_mode": "library",
            "data_binding": {"source": "sales.csv", "roles": {"category": "region", "value": "revenue"}, "aggregation": "sum", "filters": []},
        }
        create_session(job_id, {
            "job_id": job_id, "initial_prompt": "Analyze revenue", "settings": {"title": "Sales report"},
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
            **paths.session_fields(),
        })
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("no model calls")):
            run_script_generation(job_id)
        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed", session.get("error"))
        pdf = Path(session["pdf_path"])
        self.assertEqual(pdf.parent.resolve(), paths.output.resolve())
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
        self.assertEqual(Path(session["script_path"]).parent.resolve(), paths.scripts.resolve())
        self.assertFalse(Path(f"workspace/reports/{job_id}.pdf").exists())

        with TestClient(app) as client:
            preview = client.get(f"/api/sessions/{job_id}/components/g_sales/preview", headers=signed_in_headers())
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.content.startswith(b"%PDF"))
        self.assertTrue(any(paths.output.glob(f"{job_id}_g_sales_preview.pdf")))


if __name__ == "__main__":
    unittest.main()
