"""Personal API tokens: bearer access to the caller's reports, never to account management."""
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app.main import app
from app.models.db import ApiToken, AuditEvent, User
from app.services import api_token_service
from app.services.cleanup_service import delete_entire_session
from tests.auth_helpers import create_session, make_user, signed_in_headers


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class ApiTokenTests(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.job_id = f"test-token-{uuid.uuid4().hex}"
        create_session(self.job_id, {"current_step": "completed"}, owner=self.user)
        self.addCleanup(delete_entire_session, self.job_id)

    def create(self, name="script", days=30, user=None):
        response = self.client.post("/api/tokens", json={"name": name, "expires_in_days": days},
                                    headers=signed_in_headers(user or self.user))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_token_is_shown_once_and_only_its_hash_is_stored(self):
        created = self.create()
        self.assertTrue(created["token"].startswith("rg_"))
        self.assertEqual(created["hint"], created["token"][-4:])
        listed = self.client.get("/api/tokens", headers=signed_in_headers(self.user)).json()["tokens"]
        self.assertEqual([item["id"] for item in listed], [created["id"]])
        self.assertNotIn("token", listed[0])
        with db.transaction() as tx:
            row = tx.get(ApiToken, uuid.UUID(created["id"]))
            self.assertNotIn(created["token"].encode(), row.token_hash)
            events = tx.execute(select(AuditEvent.event).where(AuditEvent.user_id == self.user.id)).scalars().all()
        self.assertIn("api_token_created", events)

    def test_bearer_token_reads_and_changes_own_reports_without_csrf(self):
        token = self.create()["token"]
        listing = self.client.get("/api/sessions", headers=bearer(token))
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertIn(self.job_id, [item["job_id"] for item in listing.json()["sessions"]])
        link = self.client.post(f"/api/sessions/{self.job_id}/share-links", json={"label": "api"}, headers=bearer(token))
        self.assertEqual(link.status_code, 201, link.text)
        with db.transaction() as tx:
            self.assertIsNotNone(tx.execute(select(ApiToken.last_used_at)).scalars().first())

    def test_tokens_cannot_reach_other_users_reports(self):
        other_token = self.create(user=make_user())["token"]
        self.assertEqual(self.client.get(f"/api/sessions/{self.job_id}", headers=bearer(other_token)).status_code, 404)

    def test_tokens_cannot_manage_the_account_keys_or_tokens(self):
        token = self.create()["token"]
        blocked = [("GET", "/api/tokens"), ("POST", "/api/tokens"), ("GET", "/api/auth/me"),
                   ("POST", "/api/auth/change-password"), ("GET", "/api/credentials"),
                   ("PUT", "/api/model-preferences"), ("GET", "/api/admin/users"),
                   ("POST", "/api/data-sources/connections")]
        for method, path in blocked:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=bearer(token), json={})
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"]["code"], "api_token_not_allowed")

    def test_invalid_revoked_expired_and_disabled_tokens_are_rejected(self):
        revoked = self.create("revoked")
        self.assertEqual(self.client.delete(f"/api/tokens/{revoked['id']}", headers=signed_in_headers(self.user)).status_code, 200)
        expired = self.create("expired")
        with db.transaction() as tx:
            tx.get(ApiToken, uuid.UUID(expired["id"])).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        disabled_user = make_user()
        disabled = self.create("disabled", user=disabled_user)
        with db.transaction() as tx:
            tx.get(User, disabled_user.id).status = "disabled"
        for token in ("rg_" + "x" * 43, revoked["token"], expired["token"], disabled["token"]):
            with self.subTest(token=token[-4:]):
                response = self.client.get("/api/sessions", headers=bearer(token))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"]["code"], "invalid_api_token")

    def test_other_authorization_schemes_fall_back_to_the_cookie(self):
        response = self.client.get("/api/sessions", headers={"Authorization": "Basic abc", **signed_in_headers(self.user)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/sessions", headers={"Authorization": "Bearer not-ours"}).status_code, 401)

    def test_validity_is_bounded_and_active_tokens_are_capped(self):
        for days in (0, 366, "30"):
            response = self.client.post("/api/tokens", json={"name": "x", "expires_in_days": days}, headers=signed_in_headers(self.user))
            self.assertEqual(response.status_code, 422, days)
        for index in range(api_token_service.MAX_ACTIVE_TOKENS):
            api_token_service.create_token(self.user.id, f"t{index}", 1)
        response = self.client.post("/api/tokens", json={"name": "one more"}, headers=signed_in_headers(self.user))
        self.assertEqual(response.status_code, 409)

    def test_users_cannot_revoke_each_others_tokens(self):
        created = self.create()
        response = self.client.delete(f"/api/tokens/{created['id']}", headers=signed_in_headers(make_user()))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/sessions", headers=bearer(created["token"])).status_code, 200)


if __name__ == "__main__":
    unittest.main()
