import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import db
from app.main import app
from app.models.db import AuditEvent, LLMUsageEvent, User
from app.services import admin_service, auth_service, cloudflare_access, job_runner, session_manager, usage_service
from app.services.cleanup_service import delete_entire_session
from app.services.llm import client
from app.services.llm.errors import LLMUsageLimitError, ProviderRequestError
from app.services.llm.providers.base import GenerationResult
from app.services.llm.providers.registry import get_adapter
from tests.auth_helpers import create_session, make_user, request_as, signed_in_headers


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.job = f"test-usage-{uuid.uuid4().hex}"
        create_session(self.job, {"job_id": self.job, "current_step": "completed"}, owner=self.user)
        self.addCleanup(delete_entire_session, self.job)

    def call(self, responses=None, retries=1):
        responses = responses or [GenerationResult(text="ok", input_tokens=11, output_tokens=4)]
        with client.llm_operation_context(job_id=self.job, user_id=self.user.id, operation="test"), \
             patch.object(client, "get_provider_client", return_value=SimpleNamespace(close=lambda: None)), \
             patch.object(get_adapter("deepseek"), "generate", side_effect=responses) as generate:
            result = client.call_llm("deepseek", "deepseek-flash", "system", "prompt", retries=retries, delay=0)
        return result, generate.call_count

    def test_events_are_authoritative_and_contain_no_content(self):
        self.assertEqual(self.call(), ("ok", 1))
        session_manager.update_session(self.job, {"llm_usage": {"calls": 999}})
        summary = session_manager.get_session(self.job)["llm_usage"]
        self.assertEqual((summary["calls"], summary["input_tokens"], summary["output_tokens"]), (1, 11, 4))
        with db.transaction() as tx:
            event = tx.execute(select(LLMUsageEvent).where(LLMUsageEvent.report_id == self.job)).scalar_one()
            self.assertEqual(event.user_id, self.user.id)
            self.assertEqual(event.status, "ok")
            self.assertNotIn("prompt", event.__table__.columns)

    def test_retry_attempts_and_unknown_tokens_are_visible(self):
        self.assertEqual(self.call([ProviderRequestError("deepseek", "retryable"), GenerationResult(text="ok")], retries=2), ("ok", 2))
        summary = usage_service.account_summary(self.user.id)
        self.assertEqual((summary["calls"], summary["failed_calls"], summary["unknown_token_calls"]), (2, 1, 2))

    def test_call_limit_survives_new_operations_and_settings_cannot_raise_it(self):
        admin_service.update_user(make_user(role="admin"), self.user.id, {"report_call_limit": 1})
        self.call()
        with self.assertRaises(LLMUsageLimitError) as error:
            self.call()
        self.assertEqual(error.exception.as_error_dict()["code"], "report_call_limit")
        self.assertEqual(usage_service.report_summary(self.job)["calls"], 1)

    def test_monthly_cap_retains_deleted_report_usage_and_resets_by_utc_month(self):
        self.call()
        admin_service.update_user(make_user(role="admin"), self.user.id, {"monthly_token_limit": 15})
        delete_entire_session(self.job)
        with self.assertRaises(LLMUsageLimitError) as error:
            usage_service.reserve(self.user.id, None, "deepseek", "deepseek-flash", "test")
        self.assertEqual(error.exception.code, "monthly_token_limit")
        with db.transaction() as tx:
            tx.execute(update(LLMUsageEvent).where(LLMUsageEvent.user_id == self.user.id)
                       .values(created_at=usage_service.month_start() - timedelta(seconds=1)))
        self.assertIsInstance(usage_service.reserve(self.user.id, None, "deepseek", "deepseek-flash", "test"), int)

    def test_pending_attempts_are_atomic_and_count_toward_cap(self):
        admin_service.update_user(make_user(role="admin"), self.user.id, {"report_call_limit": 1})
        barrier = threading.Barrier(4)
        def attempt(_):
            barrier.wait(timeout=5)
            try:
                return usage_service.reserve(self.user.id, self.job, "deepseek", "deepseek-flash", "test")
            except LLMUsageLimitError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(usage_service.report_summary(self.job)["pending_calls"], 1)

    def test_owner_cannot_be_forged_and_disabled_accounts_stop_dispatch(self):
        other = make_user()
        with self.assertRaises(LLMUsageLimitError):
            usage_service.reserve(other.id, self.job, "deepseek", "deepseek-flash", "test")
        admin_service.update_user(make_user(role="admin"), self.user.id, {"status": "disabled"})
        with self.assertRaises(LLMUsageLimitError) as error:
            self.call()
        self.assertEqual(error.exception.code, "account_disabled")
        session_manager.update_session(self.job, {"explore_history": [{"role": "assistant", "status": "pending", "content": "Aguardando"}]})
        called = []
        job_runner.run_job(lambda _: called.append(True), self.job)
        self.assertEqual(called, [])
        self.assertEqual(session_manager.get_session(self.job)["error"]["code"], "account_disabled")
        self.assertFalse(session_manager.has_pending_exploration(session_manager.get_session(self.job)))

    def test_usage_routes_are_scoped_and_private(self):
        self.call()
        other = signed_in_headers(make_user())
        with TestClient(app) as http:
            own = http.get("/api/usage", headers=signed_in_headers(self.user))
            self.assertEqual(own.json()["llm"]["input_tokens"], 11)
            self.assertEqual(http.get("/api/usage", headers=other).json()["llm"]["calls"], 0)
            self.assertEqual(http.get("/api/usage/reports", headers=other).json()["reports"], [])
            reports = http.get("/api/usage/reports", headers=signed_in_headers(self.user)).json()["reports"]
            self.assertEqual([r["job_id"] for r in reports], [self.job])
            self.assertEqual(http.get("/usage", headers=signed_in_headers(self.user)).headers["cache-control"], "private, no-store")
            self.assertEqual(http.get("/usage", follow_redirects=False).status_code, 303)


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.admin = make_user(role="admin")
        self.user = make_user()
        self.admin_headers = signed_in_headers(self.admin)
        self.user_headers = signed_in_headers(self.user)

    def test_admin_endpoints_require_role_and_csrf_and_reject_extra_fields(self):
        paths = ["/admin", "/api/admin/users", "/api/admin/usage", "/api/admin/audit"]
        with TestClient(app) as http:
            for path in paths:
                self.assertEqual(http.get(path, headers=self.user_headers).status_code, 403, path)
                allowed = http.get(path, headers=self.admin_headers)
                self.assertEqual(allowed.status_code, 200, allowed.text)
            path = f"/api/admin/users/{self.user.id}"
            self.assertEqual(http.patch(path, headers={"Cookie": self.admin_headers["Cookie"]}, json={"status": "disabled"}).status_code, 403)
            for invalid in ({"role": "admin"}, {"report_call_limit": -1}, {"report_call_limit": True}, {"status": None}):
                self.assertEqual(http.patch(path, headers=self.admin_headers, json=invalid).status_code, 422)

    def test_disable_revoke_and_reenable_does_not_restore_old_sessions(self):
        with TestClient(app) as http:
            url = f"/api/admin/users/{self.user.id}"
            self.assertEqual(http.patch(url, headers=self.admin_headers, json={"status": "disabled"}).status_code, 200)
            self.assertEqual(http.get("/api/auth/me", headers=self.user_headers).status_code, 401)
            self.assertEqual(http.patch(url, headers=self.admin_headers, json={"status": "active"}).status_code, 200)
            self.assertEqual(http.get("/api/auth/me", headers=self.user_headers).status_code, 401)
            self.assertEqual(http.get("/api/auth/me", headers=signed_in_headers(self.user)).status_code, 200)

    def test_admin_metadata_omits_credentials_and_report_contents_and_audits_mutation(self):
        secret = "sk-planted-admin-key-1234567890"
        auth_service.audit("test_redaction", self.user.id, secret=secret)
        with TestClient(app) as http:
            url = f"/api/admin/users/{self.user.id}"
            self.assertEqual(http.patch(url, headers=self.admin_headers, json={"monthly_token_limit": 100, "report_call_limit": 5}).status_code, 200)
            users = http.get("/api/admin/users?limit=100", headers=self.admin_headers)
            self.assertNotIn("password_hash", users.text)
            self.assertNotIn("preferences", users.text)
            events = http.get("/api/admin/audit?limit=2", headers=self.admin_headers).json()
            self.assertEqual(events["events"][0]["event"], "admin_user_updated")
            self.assertIsNotNone(events["next_before"])
            older = http.get(f"/api/admin/audit?before={events['next_before']}", headers=self.admin_headers)
            self.assertNotIn(secret, older.text)
            self.assertTrue(all(e["id"] < events["next_before"] for e in older.json()["events"]))

    def test_self_disable_and_last_admin_guard(self):
        with self.assertRaises(auth_service.AuthError) as error:
            admin_service.update_user(self.admin, self.admin.id, {"status": "disabled"})
        self.assertEqual(error.exception.code, "self_disable")
        with db.transaction() as tx:
            tx.execute(update(User).where(User.role == "admin", User.id != self.admin.id).values(status="disabled"))
        with self.assertRaises(auth_service.AuthError) as error:
            admin_service.update_user(self.user, self.admin.id, {"status": "disabled"})
        self.assertEqual(error.exception.code, "last_admin")

    def test_cleanup_retention_validation_and_audit(self):
        with TestClient(app) as http, patch("app.routers.api.cleanup_expired_artifacts") as cleanup:
            self.assertEqual(http.post("/api/admin/cleanup", headers=self.admin_headers, json={"sessions_days": -1}).status_code, 422)
            cleanup.assert_not_called()
            self.assertEqual(http.post("/api/admin/cleanup", headers=self.admin_headers).status_code, 200)
            cleanup.assert_called_once_with(None)
        with db.transaction() as tx:
            self.assertIsNotNone(tx.execute(select(AuditEvent).where(AuditEvent.user_id == self.admin.id,
                AuditEvent.event == "admin_cleanup_requested")).scalar_one_or_none())


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def test_signed_assertions_are_verified_and_bound_to_account(self):
        admin = make_user(role="admin")
        headers = signed_in_headers(admin)
        claims = {"iss": "https://repgen.cloudflareaccess.com", "aud": ["admin-aud"], "sub": "identity",
                  "email": admin.email, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
        def token(changes=None, key=None, algorithm="RS256"):
            return jwt.encode({**claims, **(changes or {})}, key or self.key, algorithm=algorithm, headers={"kid": "test"})
        mock_jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=self.key.public_key()))
        with patch.dict(os.environ, {"CLOUDFLARE_ACCESS_ISSUER": claims["iss"], "CLOUDFLARE_ACCESS_AUD": "admin-aud"}), \
             patch.object(cloudflare_access, "_jwks", return_value=mock_jwks), TestClient(app) as http:
            self.assertEqual(http.get("/api/admin/users", headers=headers).status_code, 403)
            for value in [token({"aud": ["other"]}), token({"iss": "https://other.cloudflareaccess.com"}),
                          token({"exp": 1}), token({"email": "other@example.com"}),
                          token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)), "invalid"]:
                self.assertEqual(http.get("/api/admin/users", headers={**headers, "Cf-Access-Jwt-Assertion": value}).status_code, 403)
            accepted = {**headers, "Cf-Access-Jwt-Assertion": token()}
            self.assertEqual(http.get("/api/admin/users", headers=accepted).status_code, 200)
            self.assertEqual(http.get("/admin", headers=accepted).status_code, 200)

    def test_production_fails_closed_without_access_configuration(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "CLOUDFLARE_ACCESS_ISSUER": "", "CLOUDFLARE_ACCESS_AUD": ""}):
            with self.assertRaises(HTTPException) as error:
                cloudflare_access.require_access(SimpleNamespace(headers={}), SimpleNamespace(email="admin@example.com"))
        self.assertEqual(error.exception.status_code, 503)
