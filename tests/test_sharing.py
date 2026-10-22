import hashlib
import json
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from fpdf import FPDF
from PIL import Image
from sqlalchemy import select

from app import db
from app.main import app
from app.models.db import ReportShareLink, User
from app.services.cleanup_service import delete_entire_session
from app.services.redaction import redact
from app.services import sharing_service
from app.services.session_manager import get_session, update_session
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import create_session, make_user, signed_in_headers


class ReportSharingTests(unittest.TestCase):
    def setUp(self):
        self.owner, self.other = make_user(), make_user()
        self.headers = signed_in_headers(self.owner)
        self.other_headers = signed_in_headers(self.other)
        self.job_id = f"test-sharing-{uuid.uuid4().hex}"
        self.paths = ReportPaths.for_new_report(self.job_id, str(self.owner.id)).ensure()
        source = self.paths.uploads / "sales.csv"; source.write_text("region,revenue\nNorth,30\n")
        processed = self.paths.filtered / "processed.csv"; processed.write_text("region,revenue\nNorth,60\n")
        pdf_path = self.paths.output / "report.pdf"
        pdf = FPDF(); pdf.add_page(); pdf.set_font("Helvetica", size=12); pdf.cell(100, 8, "Revenue report: 30"); pdf.output(pdf_path)
        self.turn_id = "a" * 32
        chart_dir = self.paths.output / f"explore_{self.job_id}_{self.turn_id}"; chart_dir.mkdir()
        Image.new("RGB", (20, 20), "white").save(chart_dir / "chart_1.png")
        create_session(self.job_id, {
            "job_id": self.job_id, **self.paths.session_fields(), "current_step": "completed", "status": "Concluído", "pdf_version": 1, "pdf_path": str(pdf_path),
            "initial_prompt": "Analyze sales", "settings": {"title": "Revenue report", "legacy_api_key": "private-setting", "language": "Portuguese"},
            "files": [{"original_filename": "sales.csv", "path": str(source), "preprocessed_path": str(processed), "mime_type": "text/csv", "source": {"password": "connector-secret"}, "artifact": {"filename": "sales.csv", "shape": {"rows": 1, "columns": 2}}}],
            "artifacts": [{"id": "summary", "type": "text", "indicator": "Summary", "reason": "Explain revenue"}],
            "components": {"summary": 'def render_summary(pdf,dataframes,x,y,w,h):\n    render_text_block(pdf, "Revenue totals 30.", x,y,w,h)'},
            "template": [{"id": "summary", "page": 1, "x": 10, "y": 30, "w": 190, "h": 30}],
            "script": f"# report code\nsource = {str(source)!r}", "calculated_metrics": {"total": 30},
            "logs": ["Report completed"], "llm_usage": {"private": "usage-details"},
            "source_snapshot_history": [{"original_filename": "sales.csv", "path": str(source), "mime_type": "text/csv", "source": {"snapshot_id": "c" * 32, "dataset_name": "Revenue", "password": "connector-secret"}}],
        }, owner=self.owner)
        self.addCleanup(delete_entire_session, self.job_id)
        self.endpoint = f"/api/sessions/{self.job_id}/share-links"

    def link(self, hours=24):
        with TestClient(app) as client:
            response = client.post(self.endpoint, json={"label": "Review", "expires_in_hours": hours}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json(); result["token"] = result["path"].split("/")[-1]
        return result

    def test_anonymous_report_exposes_content_but_no_account_connector_or_host_data(self):
        link = self.link()
        with TestClient(app, follow_redirects=False) as client:
            page = client.get(link["path"]); response = client.get(f"/api/shared-reports/{link['token']}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("location", page.headers)
        self.assertEqual(response.status_code, 200, response.text)
        shared = response.json()
        self.assertEqual(shared["settings"]["title"], "Revenue report")
        self.assertEqual(shared["calculated_metrics"], {"total": 30})
        self.assertEqual(shared["component_texts"]["summary"], "Revenue totals 30.")
        self.assertIn("render_summary", shared["components"]["summary"])
        self.assertIn("report code", shared["script"])
        self.assertTrue(shared["sharing"]["read_only"])
        for secret in ("private-setting", "connector-secret", "usage-details", str(self.paths.root), str(self.owner.id), self.owner.email):
            self.assertNotIn(secret, response.text)
        for key in ("owner_id", "storage_dir", "pdf_path", "llm_usage"):
            self.assertNotIn(key, shared)
        self.assertEqual(shared["source_snapshot_history"][0]["dataset_name"], "Revenue")
        for item in (page, response):
            self.assertEqual(item.headers["cache-control"], "private, no-store")
            self.assertEqual(item.headers["referrer-policy"], "no-referrer")
            self.assertIn("noindex", item.headers["x-robots-tag"])

    def test_tokens_are_hashed_and_lists_never_return_raw_link_tokens(self):
        link = self.link(None)
        self.assertIsNone(link["expires_at"])
        with db.transaction() as tx:
            row = tx.execute(select(ReportShareLink).where(ReportShareLink.report_id == self.job_id)).scalar_one()
            self.assertEqual(row.token_hash, hashlib.sha256(link["token"].encode()).digest())
        with TestClient(app) as client:
            listing = client.get(self.endpoint, headers=self.headers)
        self.assertNotIn(link["token"], listing.text)
        self.assertNotIn("token_hash", listing.text)
        with patch.object(sharing_service, "_now", return_value=datetime.now(timezone.utc) + timedelta(days=400)):
            with TestClient(app) as client:
                self.assertEqual(client.get(f"/api/shared-reports/{link['token']}").status_code, 200)

    def test_expiry_and_revocation_block_every_resource_and_other_links_remain_valid(self):
        expiring, permanent = self.link(1), self.link(None)
        paths = ["", "/pdf", "/files/0/preview", "/files/0/download", f"/explore/{self.turn_id}/charts/chart_1", f"/source-snapshots/{'c' * 32}/download"]
        with TestClient(app) as client:
            for suffix in paths:
                self.assertEqual(client.get(f"/api/shared-reports/{expiring['token']}{suffix}").status_code, 200, suffix)
            boundary = datetime.fromisoformat(expiring["expires_at"])
            with patch.object(sharing_service, "_now", return_value=boundary):
                for suffix in paths:
                    self.assertEqual(client.get(f"/api/shared-reports/{expiring['token']}{suffix}").status_code, 404, suffix)
                self.assertEqual(client.get(expiring["path"]).status_code, 404)
                self.assertEqual(client.get(f"/api/shared-reports/{permanent['token']}").status_code, 200)
            response = client.delete(f"{self.endpoint}/{permanent['id']}", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            for suffix in paths:
                self.assertEqual(client.get(f"/api/shared-reports/{permanent['token']}{suffix}").status_code, 404)

    def test_other_accounts_cannot_manage_links_or_edit_report_using_a_share_token(self):
        link = self.link()
        before = get_session(self.job_id)
        with TestClient(app) as client, patch("app.routers.api.run_component_edit") as edit:
            self.assertEqual(client.get(self.endpoint, headers=self.other_headers).status_code, 404)
            self.assertEqual(client.post(self.endpoint, headers=self.other_headers, json={}).status_code, 404)
            self.assertEqual(client.delete(f"{self.endpoint}/{link['id']}", headers=self.other_headers).status_code, 404)
            self.assertEqual(client.post(f"/api/sessions/{self.job_id}/components/summary/edit?token={link['token']}", json={"text": "Changed"}, headers=self.other_headers).status_code, 404)
            self.assertEqual(client.put(f"/api/shared-reports/{link['token']}", json={"settings": {}}, headers=self.other_headers).status_code, 405)
            self.assertEqual(client.post(f"/api/shared-reports/{link['token']}/edit", json={}, headers=self.other_headers).status_code, 404)
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}", headers=self.other_headers).status_code, 200)
        edit.assert_not_called()
        self.assertEqual(get_session(self.job_id), before)

    def test_public_file_access_is_bound_to_the_report_and_never_runs_generated_code(self):
        link = self.link()
        with TestClient(app) as client, patch("app.routers.api.execute_script") as execute:
            pdf = client.get(f"/api/shared-reports/{link['token']}/pdf")
            self.assertTrue(pdf.content.startswith(b"%PDF-"))
            self.assertIn("North,60", client.get(f"/api/shared-reports/{link['token']}/files/0/preview?variant=preprocessed").text)
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}/files/9/download").status_code, 404)
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}/files/0/delete").status_code, 401)
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}/explore/{self.turn_id}/charts/chart_9").status_code, 404)
            target = self.paths.uploads / "linked.csv"; target.symlink_to("/etc/passwd")
            update_session(self.job_id, {"files": [{"original_filename": "linked.csv", "path": str(target)}]})
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}/files/0/download").status_code, 404)
        execute.assert_not_called()

    def test_disabling_owner_or_deleting_report_blocks_existing_links(self):
        link = self.link(None)
        with db.transaction() as tx:
            tx.get(User, self.owner.id).status = "disabled"
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}").status_code, 404)
        with db.transaction() as tx:
            tx.get(User, self.owner.id).status = "active"
        delete_entire_session(self.job_id)
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/api/shared-reports/{link['token']}/pdf").status_code, 404)
        with db.transaction() as tx:
            self.assertIsNone(tx.get(ReportShareLink, uuid.UUID(link["id"])))

    def test_expiry_input_is_validated_and_urls_are_redacted_from_logs(self):
        with TestClient(app) as client:
            for hours in (0, -1, True, "24", 8761):
                self.assertEqual(client.post(self.endpoint, json={"expires_in_hours": hours}, headers=self.headers).status_code, 422)
            self.assertEqual(client.post(self.endpoint, json={"label": "bad\x00label"}, headers=self.headers).status_code, 422)
        token = self.link()["token"]
        self.assertNotIn(token, redact(f"GET /share/{token} HTTP/1.1"))
        self.assertNotIn(token, redact(f"GET /api/shared-reports/{token}/pdf?v=1 HTTP/1.1"))
        self.assertNotIn(token, redact(f"See (https://example.com/share/{token}#pdf)."))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/shared-reports/' + 'a' * 43).status_code, 404)
