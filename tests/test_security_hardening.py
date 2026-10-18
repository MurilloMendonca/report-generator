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
from app.services.cleanup_service import delete_entire_session
from app.services.session_manager import (
    create_session,
    get_session,
    get_session_path,
    is_valid_job_id,
    recover_interrupted_sessions,
)


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.cookies = {}
        self.client = None


class LoginRateLimitTests(unittest.TestCase):
    def setUp(self):
        security.login_rate_limiter.reset()
        self.addCleanup(security.login_rate_limiter.reset)

    def test_blocks_after_repeated_failures_with_retry_after(self):
        with TestClient(app) as client:
            for _ in range(security.LOGIN_MAX_FAILURES):
                self.assertEqual(client.post("/api/auth/login", json={"secret": "wrong"}).status_code, 401)
            blocked = client.post("/api/auth/login", json={"secret": security.APP_ACCESS_SECRET})
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["retry-after"]), 0)

    def test_successful_login_clears_failures(self):
        with TestClient(app) as client:
            for _ in range(security.LOGIN_MAX_FAILURES - 1):
                client.post("/api/auth/login", json={"secret": "wrong"})
            self.assertEqual(client.post("/api/auth/login", json={"secret": security.APP_ACCESS_SECRET}).status_code, 200)
            self.assertEqual(client.post("/api/auth/login", json={"secret": "wrong"}).status_code, 401)
            self.assertEqual(client.post("/api/auth/login", json={"secret": security.APP_ACCESS_SECRET}).status_code, 200)

    def test_window_expiry_unblocks(self):
        limiter = security.LoginRateLimiter(max_failures=2, window_seconds=60)
        with patch.object(security.time, "time", return_value=1000.0):
            limiter.record_failure("ip")
            limiter.record_failure("ip")
            self.assertGreater(limiter.retry_after("ip"), 0)
        with patch.object(security.time, "time", return_value=1061.0):
            self.assertEqual(limiter.retry_after("ip"), 0)


class LogoutRevocationTests(unittest.TestCase):
    def test_logout_revokes_header_token(self):
        token = security.create_session_token()
        headers = {"x-access-token": token}
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/auth/check", headers=headers).status_code, 200)
            self.assertEqual(client.post("/api/auth/logout", headers=headers).status_code, 200)
            self.assertEqual(client.get("/api/auth/check", headers=headers).status_code, 401)
        self.assertTrue(security.is_valid_session_token(security.create_session_token()))

    def test_revocation_survives_reload(self):
        token = security.create_session_token()
        self.assertTrue(security.revoke_session_token(token))
        security.revoked_tokens.reset()
        self.assertFalse(security.is_valid_session_token(token))

    def test_invalid_tokens_are_not_revocable(self):
        self.assertFalse(security.revoke_session_token("garbage"))


class AdminAccessTests(unittest.TestCase):
    def test_admin_secret_is_required_when_configured(self):
        with patch.object(security, "APP_ADMIN_SECRET", "admin-pass"):
            with self.assertRaises(HTTPException) as error:
                security.require_admin(FakeRequest())
            self.assertEqual(error.exception.status_code, 403)
            with self.assertRaises(HTTPException):
                security.require_admin(FakeRequest({"x-admin-secret": "wrong"}))
            security.require_admin(FakeRequest({"x-admin-secret": "admin-pass"}))

    def test_admin_is_disabled_in_production_without_secret(self):
        with patch.object(security, "APP_ADMIN_SECRET", ""), patch.object(security, "IS_DEV_ENV", False):
            with self.assertRaises(HTTPException) as error:
                security.require_admin(FakeRequest())
        self.assertEqual(error.exception.status_code, 403)

    def test_admin_route_enforces_secret(self):
        headers = {"x-access-token": security.create_session_token()}
        with patch.object(security, "APP_ADMIN_SECRET", "admin-pass"), TestClient(app) as client:
            self.assertEqual(client.get("/api/admin/workspace-usage", headers=headers).status_code, 403)
            allowed = client.get("/api/admin/workspace-usage", headers={**headers, "x-admin-secret": "admin-pass"})
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
        security.login_rate_limiter.reset()
        self.addCleanup(security.login_rate_limiter.reset)
        with patch.object(security, "CLIENT_IP_HEADER", "CF-Connecting-IP"), \
             patch.object(security, "_is_trusted_proxy", return_value=True), TestClient(app) as client:
            attacker = {"CF-Connecting-IP": "203.0.113.66"}
            for _ in range(security.LOGIN_MAX_FAILURES):
                client.post("/api/auth/login", json={"secret": "wrong"}, headers=attacker)
            self.assertEqual(client.post("/api/auth/login", json={"secret": "wrong"}, headers=attacker).status_code, 429)
            ok = client.post("/api/auth/login", json={"secret": security.APP_ACCESS_SECRET}, headers={"CF-Connecting-IP": "203.0.113.7"})
        self.assertEqual(ok.status_code, 200)


class CacheHeaderTests(unittest.TestCase):
    def test_api_and_session_pages_are_never_cached(self):
        headers = {"x-access-token": security.create_session_token()}
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/auth/check", headers=headers).headers["cache-control"], "private, no-store")
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
            get_session_path("../secrets")
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
        headers = {"x-access-token": security.create_session_token()}
        with TestClient(app) as client:
            self.assertEqual(client.delete("/api/sessions/a.b", headers=headers).status_code, 404)


class InterruptedSessionRecoveryTests(unittest.TestCase):
    def make_session(self, step):
        job_id = f"test-recover-{uuid.uuid4().hex}"
        create_session(job_id, {"job_id": job_id, "current_step": step, "progress": 40, "active_run_id": 3})
        self.addCleanup(lambda: os.path.exists(get_session_path(job_id)) and os.remove(get_session_path(job_id)))
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
