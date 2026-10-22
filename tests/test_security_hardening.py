import os
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.security as security
import app.services.job_runner as job_runner
from app.main import app
from app.services.rate_limit import LIMITS, limiter
from tests.auth_helpers import TEST_PASSWORD, csrf_headers, make_user, signed_in_headers
from app.services.cleanup_service import delete_entire_session
from app.services.session_manager import (
    get_session,
    delete_session,
    validate_job_id,
    is_valid_job_id,
    recover_interrupted_sessions,
)
from tests.auth_helpers import create_session


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.cookies = {}
        self.client = None


class SharedAccessPolicyTests(unittest.TestCase):
    def test_public_report_policy_allows_only_recognized_read_requests(self):
        token = "a" * 43
        for suffix in ("", "/pdf", "/files/0/preview", "/files/0/download", f"/explore/{'b' * 32}/charts/chart_1", f"/source-snapshots/{'c' * 32}/download"):
            path = f"/api/shared-reports/{token}{suffix}"
            self.assertTrue(security.is_public_api_request(path, "GET"))
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertFalse(security.is_public_api_request(path, method))
        for path in (f"/api/shared-reports/{token}/edit", f"/api/shared-reports/{token}/files/0/delete", f"/api/sessions/{token}", "/api/shared-reports/../pdf"):
            self.assertFalse(security.is_public_api_request(path, "GET"))


class AdminAccessTests(unittest.TestCase):
    def test_admin_routes_need_the_admin_role(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/admin/workspace-usage").status_code, 401)
            self.assertEqual(client.get("/api/admin/workspace-usage", headers=signed_in_headers()).status_code, 403)
            allowed = client.get("/api/admin/workspace-usage", headers=signed_in_headers(role="admin"))
        self.assertEqual(allowed.status_code, 200)


class ClientIpTests(unittest.TestCase):
    """Behind the Cloudflare Tunnel the peer is always cloudflared; the real
    address comes from CF-Connecting-IP, but only from a trusted proxy."""

    class Request:
        def __init__(self, peer, headers=None):
            self.client = type("Client", (), {"host": peer})()
            self.headers = headers or {}

    def key(self, peer, headers, trusted=("172.16.0.0/12",)):
        with patch.object(security, "CLIENT_IP_HEADER", "CF-Connecting-IP"), \
             patch.object(security, "TRUSTED_PROXIES", list(trusted)):
            return security.client_key(self.Request(peer, headers))

    def test_uses_header_from_trusted_proxy(self):
        self.assertEqual(self.key("172.18.0.5", {"CF-Connecting-IP": "203.0.113.9"}), "203.0.113.9")

    def test_ignores_header_from_untrusted_peer(self):
        self.assertEqual(self.key("198.51.100.7", {"CF-Connecting-IP": "203.0.113.9"}), "198.51.100.7")

    def test_ignores_malformed_header(self):
        self.assertEqual(self.key("172.18.0.5", {"CF-Connecting-IP": "not-an-ip"}), "172.18.0.5")

    def test_trusts_proxy_by_hostname(self):
        with patch.object(security, "_resolve_host", return_value={"172.18.0.9"}):
            self.assertEqual(self.key("172.18.0.9", {"CF-Connecting-IP": "2001:db8::1"}, trusted=("cloudflared",)), "2001:db8::1")
            self.assertEqual(self.key("172.18.0.10", {"CF-Connecting-IP": "2001:db8::1"}, trusted=("cloudflared",)), "172.18.0.10")

    def test_header_ignored_when_not_configured(self):
        with patch.object(security, "CLIENT_IP_HEADER", ""), patch.object(security, "TRUSTED_PROXIES", ["0.0.0.0/0"]):
            self.assertEqual(security.client_key(self.Request("172.18.0.5", {"CF-Connecting-IP": "203.0.113.9"})), "172.18.0.5")

    def test_rate_limit_is_per_real_client(self):
        limiter.reset()
        self.addCleanup(limiter.reset)
        user = make_user()
        with patch.object(security, "CLIENT_IP_HEADER", "CF-Connecting-IP"), \
             patch.object(security, "_is_trusted_proxy", return_value=True), TestClient(app) as client:
            attacker = {**csrf_headers(), "CF-Connecting-IP": "203.0.113.66"}
            for n in range(LIMITS["login_ip"][0]):
                client.post("/api/auth/login", json={"email": f"x{n}@example.com", "password": "wrong password!"}, headers=attacker)
            blocked = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}, headers=attacker)
            self.assertEqual(blocked.status_code, 429)
            ok = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD},
                             headers={**csrf_headers(), "CF-Connecting-IP": "203.0.113.7"})
        self.assertEqual(ok.status_code, 200, ok.text)


class CacheHeaderTests(unittest.TestCase):
    def test_api_and_session_pages_are_never_cached(self):
        headers = signed_in_headers()
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/auth/me", headers=headers).headers["cache-control"], "private, no-store")
            self.assertEqual(client.get("/api/sessions", headers=headers).headers["cache-control"], "private, no-store")
            self.assertNotIn("no-store", client.get("/static/css/styles.css").headers.get("cache-control", ""))


class SecurityHeaderTests(unittest.TestCase):
    def test_script_policy_disallows_inline_scripts(self):
        with TestClient(app) as client:
            policy = client.get("/").headers["content-security-policy"]
        script_policy = next(part for part in policy.split(";") if part.strip().startswith("script-src"))
        self.assertNotIn("unsafe-inline", script_policy)


class JobIdValidationTests(unittest.TestCase):
    def test_rejects_ids_that_escape_or_glob(self):
        for job_id in ["", "..", "../etc", "a/b", "a.b", "-leading", "x" * 129, None, 5]:
            self.assertFalse(is_valid_job_id(job_id), job_id)
            self.assertIsNone(get_session(job_id))
        with self.assertRaises(ValueError):
            validate_job_id("../secrets")
        self.assertTrue(is_valid_job_id(str(uuid.uuid4())))

    def test_deleting_a_short_id_does_not_remove_other_sessions_files(self):
        prefix = f"t{uuid.uuid4().hex[:6]}"
        victim_path = os.path.join("workspace", "reports", f"{prefix}abc.pdf")
        with open(victim_path, "w", encoding="utf-8") as handle:
            handle.write("%PDF-other-session")
        self.addCleanup(lambda: os.path.exists(victim_path) and os.remove(victim_path))
        delete_entire_session(prefix)
        self.assertTrue(os.path.exists(victim_path))

    def test_delete_route_rejects_invalid_id(self):
        headers = signed_in_headers()
        with TestClient(app) as client:
            self.assertEqual(client.delete("/api/sessions/a.b", headers=headers).status_code, 404)


class InterruptedSessionRecoveryTests(unittest.TestCase):
    def make_session(self, step):
        job_id = f"test-recover-{uuid.uuid4().hex}"
        create_session(job_id, {"job_id": job_id, "current_step": step, "progress": 40, "active_run_id": 3})
        self.addCleanup(delete_session, job_id)
        return job_id

    def test_marks_in_progress_sessions_failed_and_leaves_finished_ones(self):
        running = self.make_session("script")
        completed = self.make_session("completed")
        recovered = recover_interrupted_sessions()

        self.assertIn(running, recovered)
        self.assertNotIn(completed, recovered)
        session = get_session(running)
        self.assertEqual(session["current_step"], "failed")
        self.assertEqual(session["error"]["code"], "interrupted")
        self.assertEqual(session["error"]["stage"], "script")
        self.assertEqual(session["active_run_id"], 0)
        self.assertEqual(get_session(completed)["current_step"], "completed")


class JobRunnerTests(unittest.TestCase):
    def test_limits_concurrent_jobs(self):
        active = []
        peak = []
        lock = threading.Lock()

        def job(_job_id):
            with lock:
                active.append(1)
                peak.append(len(active))
            time.sleep(0.05)
            with lock:
                active.pop()

        with patch.object(job_runner, "_job_slots", threading.BoundedSemaphore(2)), \
             patch.object(job_runner, "add_log"):
            threads = [threading.Thread(target=job_runner.run_job, args=(job, f"job-{i}")) for i in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertLessEqual(max(peak), 2)
        self.assertEqual(len(peak), 6)

    def test_failed_job_releases_its_slot(self):
        def boom(_job_id):
            raise RuntimeError("boom")

        slots = threading.BoundedSemaphore(1)
        with patch.object(job_runner, "_job_slots", slots), patch.object(job_runner.logger, "exception"):
            job_runner.run_job(boom, "job-1")
        self.assertTrue(slots.acquire(blocking=False))


if __name__ == "__main__":
    unittest.main()
