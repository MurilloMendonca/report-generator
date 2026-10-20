"""Two users must not see, change, download or run anything of each other's.

RouteCoverageTests walks every route in the app. A route is either listed in
NOT_REPORT_SCOPED with a reason, or it takes a report id and must answer 404
when another user asks for it. A new route that fits neither fails here, so an
ownership check can't be forgotten.
"""
import io
import shutil
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import unittest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.routers.api as api
from app.main import app
from app.security import PUBLIC_API_PATHS
from app.services import auth_service, job_runner, quotas, session_manager
from app.services.cleanup_service import delete_entire_session
from app.utils.report_paths import TENANTS_DIR, ReportPaths
from app.utils.template_library import load_layout_templates, upsert_layout_template
from tests.auth_helpers import TEST_PASSWORD, create_session, make_user, signed_in_headers

# Routes that don't take a report id, and why that's safe.
NOT_REPORT_SCOPED = {
    ("GET", "/sources"): "signed-in source settings page (test_saved_sources)",
    ("GET", "/api/data-sources"): "caller connections and datasets only (test_saved_sources)",
    ("POST", "/api/data-sources/connections"): "creates a caller-owned connection (test_saved_sources)",
    ("PUT", "/api/data-sources/connections/{connection_id}"): "caller-owned connection (test_saved_sources)",
    ("DELETE", "/api/data-sources/connections/{connection_id}"): "caller-owned connection (test_saved_sources)",
    ("POST", "/api/data-sources/connections/{connection_id}/test"): "caller-owned connection (test_saved_sources)",
    ("POST", "/api/data-sources/datasets"): "creates a caller-owned dataset (test_saved_sources)",
    ("PUT", "/api/data-sources/datasets/{dataset_id}"): "caller-owned dataset (test_saved_sources)",
    ("DELETE", "/api/data-sources/datasets/{dataset_id}"): "caller-owned dataset (test_saved_sources)",
    ("POST", "/api/data-sources/datasets/{dataset_id}/preview"): "caller-owned dataset (test_saved_sources)",

    ("GET", "/"): "page; data comes from scoped API calls",
    ("GET", "/templates"): "page",
    ("GET", "/account"): "page",
    ("GET", "/login"): "public page",
    ("GET", "/signup"): "public page",
    ("GET", "/verify-email"): "public page",
    ("GET", "/forgot-password"): "public page",
    ("GET", "/reset-password"): "public page",
    ("GET", "/confirm-email"): "public page",
    ("GET", "/api/models"): "caller credential catalog (test_credentials)",
    ("GET", "/keys"): "signed-in page; data comes from caller-scoped APIs",
    ("GET", "/api/credentials"): "caller keys only (test_credentials)",
    ("PUT", "/api/credentials/{provider}"): "caller keys only (test_credentials)",
    ("POST", "/api/credentials/{provider}/test"): "caller keys only (test_credentials)",
    ("DELETE", "/api/credentials/{provider}"): "caller keys only (test_credentials)",
    ("GET", "/api/model-preferences"): "caller preferences only (test_credentials)",
    ("PUT", "/api/model-preferences"): "caller preferences and keys only (test_credentials)",
    ("GET", "/api/component-library"): "static catalog",
    ("GET", "/api/component-library/previews"): "static catalog",
    ("GET", "/api/component-library/presets/{preset_id}/preview"): "static catalog",
    ("GET", "/api/sessions"): "lists the caller's reports (ListingTests)",
    ("POST", "/api/sessions"): "creates a report owned by the caller",
    ("GET", "/api/usage"): "the caller's own usage",
    ("GET", "/usage"): "signed-in usage page (test_operations)",
    ("GET", "/admin"): "admin role and Access (test_operations)",
    ("GET", "/api/usage/reports"): "caller reports only (test_operations)",
    ("GET", "/api/admin/users"): "admin role and Access; metadata only (test_operations)",
    ("PATCH", "/api/admin/users/{user_id}"): "admin role and Access (test_operations)",
    ("GET", "/api/admin/usage"): "admin role and Access; aggregate only (test_operations)",
    ("GET", "/api/admin/audit"): "admin role and Access (test_operations)",
    ("GET", "/api/layout-templates"): "built-ins plus the caller's (TemplateIsolationTests)",
    ("POST", "/api/layout-templates"): "creates a template owned by the caller",
    ("PUT", "/api/layout-templates/{template_id}"): "TemplateIsolationTests",
    ("DELETE", "/api/layout-templates/{template_id}"): "TemplateIsolationTests",
    ("GET", "/api/admin/workspace-usage"): "admin role (test_security_hardening)",
    ("POST", "/api/admin/cleanup"): "admin role (test_security_hardening)",
}
ACCOUNT_SCOPED_PREFIX = "/api/auth/"  # public, or acts on the caller's own account (test_auth)

# A plausible request for each report route, so validation passes and the
# ownership check is what answers.
SAMPLE_REQUESTS = {
    ("POST", "/api/sessions/{job_id}/sources/refresh"): {"json": {"sources": [{"dataset_id": "11111111-1111-1111-1111-111111111111"}]}},
    ("GET", "/api/sessions/{job_id}/source-snapshots/{snapshot_id}/download"): {},
    ("GET", "/session/{job_id}"): {},
    ("GET", "/api/sessions/{job_id}"): {},
    ("DELETE", "/api/sessions/{job_id}"): {},
    ("POST", "/api/sessions/{job_id}/files"): {"files": {"files": ("x.csv", b"a\n1\n", "text/csv")}},
    ("GET", "/api/sessions/{job_id}/files/{file_index}/download"): {},
    ("GET", "/api/sessions/{job_id}/files/{file_index}/preview"): {},
    ("DELETE", "/api/sessions/{job_id}/files/{file_index}"): {},
    ("POST", "/api/sessions/{job_id}/chat"): {"data": {"prompt": "make it blue"}},
    ("PUT", "/api/sessions/{job_id}/artifacts"): {"json": {"artifacts": [], "components": {}}},
    ("PUT", "/api/sessions/{job_id}/template"): {"json": []},
    ("PUT", "/api/sessions/{job_id}/settings"): {"json": {"title": "x"}},
    ("POST", "/api/sessions/{job_id}/regenerate"): {"json": {"scope": "all"}},
    ("GET", "/api/sessions/{job_id}/pdf"): {},
    ("GET", "/api/sessions/{job_id}/components/{component_id}/preview"): {},
    ("POST", "/api/sessions/{job_id}/explore"): {"data": {"prompt": "how many rows?"}},
    ("GET", "/api/sessions/{job_id}/explore/{turn_id}/charts/{chart_id}"): {},
}
TURN_ID = "a" * 32


def owned_report(owner) -> tuple[str, ReportPaths]:
    """A finished report with real files: an upload, a PDF and an explorer chart."""
    job_id = f"test-iso-{uuid.uuid4().hex}"
    paths = ReportPaths.for_new_report(job_id, str(owner.id)).ensure()
    upload = paths.uploads / "data.csv"
    upload.write_text("a\n1\n")
    pdf = paths.output / f"{job_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4 secret")
    chart_dir = paths.output / f"explore_{job_id}_{TURN_ID}"
    chart_dir.mkdir()
    (chart_dir / "chart_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    create_session(job_id, {
        "job_id": job_id, "initial_prompt": "secret plan", "current_step": "completed",
        "files": [{"original_filename": "data.csv", "path": str(upload)}],
        "pdf_path": str(pdf), "pdf_version": 1,
        "artifacts": [{"id": "t_intro", "type": "text", "indicator": "x"}],
        "components": {"t_intro": "def render_t_intro(pdf, x, y, w, h): pass"},
        "explore_history": [{"role": "assistant", "turn_id": TURN_ID, "content": "x", "charts": [{"id": "chart_1"}]}],
        **paths.session_fields(),
    }, owner=owner)
    return job_id, paths


class IsolationTestCase(unittest.TestCase):
    def setUp(self):
        self.alice, self.bob = make_user(), make_user()
        self.job_id, self.paths = owned_report(self.alice)
        self.addCleanup(delete_entire_session, self.job_id)
        self.addCleanup(shutil.rmtree, self.paths.root, True)
        self.as_alice, self.as_bob = signed_in_headers(self.alice), signed_in_headers(self.bob)


class RouteCoverageTests(IsolationTestCase):
    def report_routes(self):
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, route.path

    def test_every_route_is_classified(self):
        unclassified = [
            (method, path) for method, path in self.report_routes()
            if (method, path) not in NOT_REPORT_SCOPED
            and not path.startswith(ACCOUNT_SCOPED_PREFIX)
            and (method, path) not in SAMPLE_REQUESTS
        ]
        self.assertEqual(unclassified, [], "New route: add an ownership check and a SAMPLE_REQUESTS entry, or justify it in NOT_REPORT_SCOPED")
        for method, path in SAMPLE_REQUESTS:
            self.assertIn("{job_id}", path, (method, path))
        for path in PUBLIC_API_PATHS:
            self.assertTrue(path.startswith(ACCOUNT_SCOPED_PREFIX), path)

    def test_other_users_get_404_on_every_report_route(self):
        values = {"snapshot_id": "a" * 32, "job_id": self.job_id, "file_index": "0", "component_id": "t_intro", "turn_id": TURN_ID, "chart_id": "chart_1"}
        with patch.object(api, "run_pipeline") as pipeline, patch.object(api, "run_script_generation") as render, \
             patch.object(api, "process_chat_refinement") as refine, patch.object(api, "complete_exploration_job") as explore, \
             TestClient(app, follow_redirects=False) as client:
            for (method, path), sample in SAMPLE_REQUESTS.items():
                url = path.format(**values)
                response = client.request(method, url, headers=self.as_bob, **sample)
                self.assertEqual(response.status_code, 404, f"{method} {path}: {response.status_code} {response.text[:200]}")
                self.assertNotIn("secret", response.text, f"{method} {path}")
        for job in (pipeline, render, refine, explore):
            job.assert_not_called()
        # Nothing of Alice's changed.
        self.assertEqual(session_manager.get_session(self.job_id)["initial_prompt"], "secret plan")
        self.assertTrue((self.paths.uploads / "data.csv").exists())
        self.assertEqual(len(list(self.paths.uploads.iterdir())), 1)

    def test_the_owner_can_reach_the_same_files(self):
        with TestClient(app) as client:
            for url in (f"/api/sessions/{self.job_id}", f"/api/sessions/{self.job_id}/pdf",
                        f"/api/sessions/{self.job_id}/files/0/download", f"/session/{self.job_id}"):
                self.assertEqual(client.get(url, headers=self.as_alice).status_code, 200, url)


class ListingTests(IsolationTestCase):
    def test_recent_reports_show_only_your_own(self):
        bobs_job, bobs_paths = owned_report(self.bob)
        self.addCleanup(delete_entire_session, bobs_job)
        with TestClient(app) as client:
            alice_ids = {item["job_id"] for item in client.get("/api/sessions", headers=self.as_alice).json()["sessions"]}
            bob_ids = {item["job_id"] for item in client.get("/api/sessions", headers=self.as_bob).json()["sessions"]}
        self.assertIn(self.job_id, alice_ids)
        self.assertNotIn(bobs_job, alice_ids)
        self.assertEqual(bob_ids, {bobs_job})

    def test_new_reports_belong_to_their_creator_and_live_in_their_folder(self):
        with patch.object(api, "run_pipeline"), TestClient(app) as client:
            response = client.post("/api/sessions", headers=self.as_bob, data={"provider": "deepseek"},
                                   files={"files": ("x.csv", b"a\n1\n", "text/csv")})
        job_id = response.json()["job_id"]
        self.addCleanup(delete_entire_session, job_id)
        self.assertEqual(session_manager.get_report_owner(job_id), self.bob.id)
        root = ReportPaths.from_session(session_manager.get_session(job_id)).root
        self.assertEqual(root.parent.parent.name, str(self.bob.id))
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/api/sessions/{job_id}", headers=self.as_alice).status_code, 404)


class TemplateIsolationTests(IsolationTestCase):
    def test_templates_are_private_and_built_ins_read_only(self):
        with TestClient(app) as client:
            created = client.post("/api/layout-templates", headers=self.as_alice,
                                  json={"id": "data_heavy", "name": "Alice's layout", "slots": []}).json()
            self.assertNotEqual(created["id"], "data_heavy")  # ids are always assigned by the server
            bob_view = [t["id"] for t in client.get("/api/layout-templates", headers=self.as_bob).json()["templates"]]
            self.assertNotIn(created["id"], bob_view)
            self.assertIn("data_heavy", bob_view)

            url = f"/api/layout-templates/{created['id']}"
            self.assertEqual(client.put(url, headers=self.as_bob, json={"name": "mine now", "slots": []}).status_code, 404)
            self.assertEqual(client.delete(url, headers=self.as_bob).status_code, 404)
            builtin = client.put("/api/layout-templates/data_heavy", headers=self.as_alice, json={"name": "x", "slots": []})
            self.assertEqual((builtin.status_code, builtin.json()["detail"]["code"]), (403, "builtin_template"))
            self.assertEqual(client.delete("/api/layout-templates/data_heavy", headers=self.as_alice).status_code, 404)
            self.assertEqual(client.put(url, headers=self.as_alice, json={"name": "Renamed", "slots": []}).status_code, 200)
        self.assertEqual(next(t for t in load_layout_templates(self.alice.id) if t["id"] == created["id"])["name"], "Renamed")

    def test_pipeline_cannot_use_someone_elses_template(self):
        from app.utils.template_library import select_layout_template
        bobs = upsert_layout_template({"name": "Bob only", "slots": []}, self.bob.id)
        chosen = select_layout_template([], bobs["id"], self.alice.id)
        self.assertNotEqual(chosen["id"], bobs["id"])
        self.assertEqual(select_layout_template([], bobs["id"], self.bob.id)["id"], bobs["id"])


class QuotaTests(IsolationTestCase):
    def upload(self, client, headers):
        return client.post("/api/sessions", headers=headers, data={"provider": "deepseek"},
                           files={"files": ("x.csv", b"a\n1\n", "text/csv")})

    def test_storage_quota(self):
        quotas.refresh_storage(self.job_id)
        self.assertGreater(session_manager.storage_used(self.alice.id), 0)
        with patch.object(quotas, "STORAGE_BYTES", session_manager.storage_used(self.alice.id) + 2), \
             patch.object(api, "run_pipeline"), TestClient(app) as client:
            blocked = self.upload(client, self.as_alice)
            self.assertEqual((blocked.status_code, blocked.json()["detail"]["code"]), (413, "storage_quota"))
            added = client.post(f"/api/sessions/{self.job_id}/files", headers=self.as_alice,
                                files={"files": ("more.csv", b"a\n1\n2\n3\n", "text/csv")})
            self.assertEqual(added.status_code, 413)
            # Other users have their own allowance.
            ok = self.upload(client, self.as_bob)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.addCleanup(delete_entire_session, ok.json()["job_id"])

    def test_daily_report_limit(self):
        with patch.object(quotas, "REPORTS_PER_DAY", 1), patch.object(api, "run_pipeline"), TestClient(app) as client:
            blocked = self.upload(client, self.as_alice)  # Alice already has one report today
            self.assertEqual((blocked.status_code, blocked.json()["detail"]["code"]), (429, "daily_report_limit"))
            usage = client.get("/api/usage", headers=self.as_alice).json()
        self.assertEqual(usage["reports_last_24h"], 1)

    def test_one_job_per_user_at_a_time(self):
        import threading
        started, release = threading.Event(), threading.Event()
        order = []

        def slow(job_id):
            order.append(("start", job_id))
            started.set()
            release.wait(5)
            order.append(("end", job_id))

        second_job, second_paths = owned_report(self.alice)
        self.addCleanup(delete_entire_session, second_job)
        bobs_job, _ = owned_report(self.bob)
        self.addCleanup(delete_entire_session, bobs_job)
        with patch.object(job_runner, "_job_slots", threading.BoundedSemaphore(5)):
            first = threading.Thread(target=job_runner.run_job, args=(slow, self.job_id))
            first.start()
            started.wait(5)
            second = threading.Thread(target=job_runner.run_job, args=(lambda job_id: order.append(("start", job_id)), second_job))
            second.start()
            # Bob isn't held up by Alice's job.
            job_runner.run_job(lambda job_id: order.append(("start", job_id)), bobs_job)
            self.assertIn(("start", bobs_job), order)
            self.assertNotIn(("start", second_job), order)
            self.assertIn("Aguardando seu relatório anterior", session_manager.get_session(second_job)["status"])
            release.set()
            first.join(5)
            second.join(5)
        self.assertLess(order.index(("end", self.job_id)), order.index(("start", second_job)))


class AccountDeletionTests(IsolationTestCase):
    def test_deleting_an_account_removes_its_reports_and_nothing_else(self):
        bobs_job, bobs_paths = owned_report(self.bob)
        self.addCleanup(delete_entire_session, bobs_job)
        upsert_layout_template({"name": "Alice's", "slots": []}, self.alice.id)
        with TestClient(app) as client:
            wrong = client.request("DELETE", "/api/auth/account", headers=self.as_alice, json={"password": "wrong password!!"})
            self.assertEqual(wrong.status_code, 403)
            response = client.request("DELETE", "/api/auth/account", headers=self.as_alice, json={"password": TEST_PASSWORD})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["reports_deleted"], 1)
            self.assertEqual(client.get("/api/auth/me", headers=self.as_alice).status_code, 401)
        self.assertIsNone(auth_service.get_user(self.alice.id))
        self.assertIsNone(session_manager.get_session(self.job_id))
        self.assertFalse((TENANTS_DIR / str(self.alice.id)).exists())
        self.assertIsNotNone(session_manager.get_session(bobs_job))
        self.assertTrue((bobs_paths.uploads / "data.csv").exists())

    def test_the_last_admin_cannot_delete_their_account(self):
        from app import db
        from app.models.db import User
        from sqlalchemy import update
        with db.transaction() as tx:  # make this the only admin
            tx.execute(update(User).where(User.role == "admin").values(role="user"))
        admin = make_user(role="admin")
        with TestClient(app) as client:
            response = client.request("DELETE", "/api/auth/account", headers=signed_in_headers(admin), json={"password": TEST_PASSWORD})
        self.assertEqual((response.status_code, response.json()["detail"]["code"]), (409, "last_admin"))


if __name__ == "__main__":
    unittest.main()
