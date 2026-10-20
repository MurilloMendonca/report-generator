import hashlib
import io
import re
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, update

import app.routers.api as api
from app import db
from app.cli import main as cli_main
from app.main import app
from app.models.db import AuditEvent, AuthSession, EmailToken, User
from app.services import auth_service, email_service, turnstile
from app.services.rate_limit import LIMITS, limiter
from tests.auth_helpers import TEST_PASSWORD, make_user, signed_in_headers

NEW_PASSWORD = "another long passphrase"


def new_email() -> str:
    return f"auth-{uuid.uuid4().hex[:12]}@example.com"


def browser() -> TestClient:
    """A client that keeps cookies and echoes the CSRF cookie, like api.js does."""
    client = TestClient(app)
    client.get("/api/auth/config")
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    return client


def link_token(message) -> str:
    return re.search(r"#token=([A-Za-z0-9_-]+)", message.body).group(1)


def last_email(to: str):
    return next(message for message in reversed(email_service.outbox) if message.to == to)


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        email_service.outbox.clear()
        self.addCleanup(limiter.reset)

    def signup_open(self):
        return patch.dict("os.environ", {"PDF_REPORT_SIGNUP_MODE": "open"})

    def login(self, client, email, password=TEST_PASSWORD, **extra):
        return client.post("/api/auth/login", json={"email": email, "password": password, **extra})


class SignupAndVerificationTests(AuthTestCase):
    def test_signup_verify_and_create_reports(self):
        email = new_email()
        with self.signup_open(), browser() as client:
            response = client.post("/api/auth/signup", json={"email": email.upper(), "password": TEST_PASSWORD, "display_name": "Ana"})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertNotIn("pdf_report_session", response.cookies)

            self.assertEqual(self.login(client, email).status_code, 200)
            me = client.get("/api/auth/me").json()["user"]
            self.assertEqual((me["email"], me["display_name"], me["email_verified"]), (email, "Ana", False))

            # Unverified accounts can sign in but not create reports.
            with patch.object(api, "run_pipeline"):
                blocked = client.post("/api/sessions", files={"files": ("a.csv", b"a\n1\n", "text/csv")})
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(blocked.json()["detail"]["code"], "email_not_verified")

            token = link_token(last_email(email))
            self.assertEqual(client.post("/api/auth/verify-email", json={"token": token}).status_code, 200)
            self.assertTrue(client.get("/api/auth/me").json()["user"]["email_verified"])
            reused = client.post("/api/auth/verify-email", json={"token": token})
            self.assertEqual(reused.status_code, 400)
            self.assertEqual(reused.json()["detail"]["code"], "invalid_token")

    def test_signup_with_a_taken_email_looks_the_same(self):
        user = make_user()
        with self.signup_open(), browser() as client:
            fresh = client.post("/api/auth/signup", json={"email": new_email(), "password": TEST_PASSWORD})
            taken = client.post("/api/auth/signup", json={"email": user.email, "password": TEST_PASSWORD})
        self.assertEqual((fresh.status_code, fresh.json()), (taken.status_code, taken.json()))
        self.assertIn("já está cadastrado", last_email(user.email).body)
        self.assertEqual(auth_service.get_user_by_email(user.email).password_hash, user.password_hash)

    def test_signup_validation_and_closed_mode(self):
        with browser() as client:
            closed = client.post("/api/auth/signup", json={"email": new_email(), "password": TEST_PASSWORD})
            self.assertEqual((closed.status_code, closed.json()["detail"]["code"]), (403, "signup_closed"))
            with self.signup_open():
                short = client.post("/api/auth/signup", json={"email": new_email(), "password": "short"})
                bad = client.post("/api/auth/signup", json={"email": "not-an-email", "password": TEST_PASSWORD})
        self.assertEqual(short.json()["detail"]["code"], "weak_password")
        self.assertEqual(bad.json()["detail"]["code"], "invalid_email")

    def test_resend_verification_replaces_the_old_link(self):
        user = make_user(verified=False)
        with browser() as client:
            self.login(client, user.email)
            client.post("/api/auth/resend-verification")
            first = link_token(last_email(user.email))
            client.post("/api/auth/resend-verification")
            second = link_token(last_email(user.email))
            self.assertEqual(client.post("/api/auth/verify-email", json={"token": first}).status_code, 400)
            self.assertEqual(client.post("/api/auth/verify-email", json={"token": second}).status_code, 200)

    def test_expired_and_tampered_links_are_rejected(self):
        user = make_user(verified=False)
        auth_service.send_verification(user)
        token = link_token(last_email(user.email))
        with browser() as client:
            self.assertEqual(client.post("/api/auth/verify-email", json={"token": token[:-2] + "xx"}).status_code, 400)
            with db.transaction() as tx:
                tx.execute(update(EmailToken).where(EmailToken.user_id == user.id).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            self.assertEqual(client.post("/api/auth/verify-email", json={"token": token}).status_code, 400)

    def test_tokens_and_passwords_are_stored_hashed(self):
        user = make_user(verified=False)
        auth_service.send_verification(user)
        token = link_token(last_email(user.email))
        with db.transaction() as tx:
            stored = tx.execute(select(EmailToken.token_hash).where(EmailToken.user_id == user.id)).scalar_one()
        self.assertEqual(stored, hashlib.sha256(token.encode()).digest())
        self.assertTrue(user.password_hash.startswith("$argon2id$"))
        self.assertNotIn(TEST_PASSWORD, user.password_hash)


class LoginAndSessionTests(AuthTestCase):
    def test_login_cookie_logout_and_wrong_password(self):
        user = make_user()
        with browser() as client:
            wrong = self.login(client, user.email, "wrong password here")
            self.assertEqual((wrong.status_code, wrong.json()["detail"]["code"]), (401, "invalid_credentials"))
            unknown = self.login(client, new_email())
            self.assertEqual(unknown.json(), wrong.json())

            response = self.login(client, user.email)
            self.assertEqual(response.status_code, 200)
            cookie = next(value for value in response.headers.get_list("set-cookie") if value.startswith("pdf_report_session="))
            self.assertIn("HttpOnly", cookie)
            self.assertIn("samesite=lax", cookie.lower())
            self.assertEqual(client.get("/api/sessions").status_code, 200)

            self.assertEqual(client.post("/api/auth/logout").status_code, 200)
            self.assertEqual(client.get("/api/sessions").status_code, 401)

    def test_each_login_gets_a_new_session_and_the_old_one_ends(self):
        user = make_user()
        with browser() as client:
            self.login(client, user.email)
            first = client.cookies["pdf_report_session"]
            self.login(client, user.email)
            second = client.cookies["pdf_report_session"]
        self.assertNotEqual(first, second)
        self.assertIsNone(auth_service.resolve_session(first))
        self.assertIsNotNone(auth_service.resolve_session(second))

    def test_idle_and_absolute_expiry(self):
        user = make_user()
        for column in ("idle_expires_at", "absolute_expires_at"):
            token = auth_service.start_session(user)
            with db.transaction() as tx:
                tx.execute(update(AuthSession).where(AuthSession.token_hash == hashlib.sha256(token.encode()).digest())
                           .values(**{column: datetime.now(timezone.utc) - timedelta(seconds=1)}))
            self.assertIsNone(auth_service.resolve_session(token), column)

    def test_use_extends_the_idle_expiry_but_not_past_the_absolute_one(self):
        user = make_user()
        token = auth_service.start_session(user)
        token_hash = hashlib.sha256(token.encode()).digest()
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        with db.transaction() as tx:
            tx.execute(update(AuthSession).where(AuthSession.token_hash == token_hash).values(last_seen_at=stale))
        session = auth_service.resolve_session(token).session
        self.assertGreater(session.last_seen_at, stale)
        self.assertLessEqual(session.idle_expires_at, session.absolute_expires_at)

    def test_disabled_accounts_are_signed_out(self):
        user = make_user()
        token = auth_service.start_session(user)
        with db.transaction() as tx:
            tx.execute(update(User).where(User.id == user.id).values(status="disabled"))
        self.assertIsNone(auth_service.resolve_session(token))
        with browser() as client:
            self.assertEqual(self.login(client, user.email).status_code, 401)

    def test_device_list_revoke_one_and_logout_everywhere(self):
        user = make_user()
        other = auth_service.start_session(user, "198.51.100.4", "Mozilla/5.0 (Windows NT 10.0) Chrome/140")
        with browser() as client:
            self.login(client, user.email)
            sessions = client.get("/api/auth/sessions").json()["sessions"]
            self.assertEqual(len(sessions), 2)
            current = [item for item in sessions if item["current"]]
            remote = [item for item in sessions if not item["current"]][0]
            self.assertEqual(len(current), 1)
            self.assertEqual(remote["ip"], "198.51.100.4")

            self.assertEqual(client.delete(f"/api/auth/sessions/{remote['id']}").status_code, 200)
            self.assertIsNone(auth_service.resolve_session(other))
            self.assertEqual(client.delete(f"/api/auth/sessions/{remote['id']}").status_code, 404)
            self.assertEqual(client.delete("/api/auth/sessions/not-a-uuid").status_code, 404)

            third = auth_service.start_session(user)
            self.assertEqual(client.post("/api/auth/logout-all").status_code, 200)
            self.assertIsNone(auth_service.resolve_session(third))
            self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_cannot_revoke_someone_elses_session(self):
        victim = make_user()
        victim_token = auth_service.start_session(victim)
        victim_session_id = auth_service.resolve_session(victim_token).session.id
        with browser() as client:
            self.login(client, make_user().email)
            self.assertEqual(client.delete(f"/api/auth/sessions/{victim_session_id}").status_code, 404)
        self.assertIsNotNone(auth_service.resolve_session(victim_token))

    def test_invalid_cookie_is_cleared(self):
        with TestClient(app) as client:
            response = client.get("/api/auth/me", headers={"Cookie": "pdf_report_session=forged"})
        self.assertEqual(response.status_code, 401)
        self.assertTrue(any(value.startswith('pdf_report_session=""') or "Max-Age=0" in value
                            for value in response.headers.get_list("set-cookie")))

    def test_audit_trail(self):
        user = make_user()
        with browser() as client:
            self.login(client, user.email, "wrong password here")
            self.login(client, user.email)
        with db.transaction() as tx:
            events = list(tx.execute(select(AuditEvent.event).where(AuditEvent.user_id == user.id).order_by(AuditEvent.id)).scalars())
        self.assertEqual(events[-2:], ["login_failed", "login"])


class PasswordTests(AuthTestCase):
    def test_forgot_and_reset_password_signs_out_everywhere(self):
        user = make_user(verified=False)
        elsewhere = auth_service.start_session(user)
        with browser() as client:
            known = client.post("/api/auth/forgot-password", json={"email": user.email})
            unknown = client.post("/api/auth/forgot-password", json={"email": new_email()})
            self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))
            self.assertEqual([m.to for m in email_service.outbox], [user.email])

            token = link_token(last_email(user.email))
            weak = client.post("/api/auth/reset-password", json={"token": token, "password": "short"})
            self.assertEqual(weak.status_code, 400)
            reset = client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual(client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}).status_code, 400)

            self.assertIsNone(auth_service.resolve_session(elsewhere))
            self.assertEqual(self.login(client, user.email).status_code, 401)
            self.assertEqual(self.login(client, user.email, NEW_PASSWORD).status_code, 200)
            # The reset link proved the address, so the account is now verified.
            self.assertTrue(client.get("/api/auth/me").json()["user"]["email_verified"])

    def test_reset_links_expire_after_30_minutes(self):
        self.assertEqual(auth_service.TOKEN_LIFETIMES["reset_password"], timedelta(minutes=30))

    def test_change_password_keeps_this_session_and_ends_others(self):
        user = make_user()
        elsewhere = auth_service.start_session(user)
        with browser() as client:
            self.login(client, user.email)
            wrong = client.post("/api/auth/change-password", json={"current_password": "nope nope nope", "new_password": NEW_PASSWORD})
            self.assertEqual(wrong.status_code, 403)
            ok = client.post("/api/auth/change-password", json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(client.get("/api/auth/me").status_code, 200)
        self.assertIsNone(auth_service.resolve_session(elsewhere))
        self.assertIn("alterada", last_email(user.email).subject)
        self.assertIsNotNone(auth_service.authenticate(user.email, NEW_PASSWORD))

    def test_oversized_password_is_rejected_cheaply(self):
        with self.assertRaises(auth_service.AuthError):
            auth_service.validate_password("x" * 257)
        self.assertIsNone(auth_service.authenticate(make_user().email, "x" * 900))


class EmailChangeTests(AuthTestCase):
    def test_change_email_needs_the_password_and_a_confirmed_link(self):
        user = make_user()
        target = new_email()
        with browser() as client:
            self.login(client, user.email)
            wrong = client.post("/api/auth/change-email", json={"new_email": target, "password": "wrong password here"})
            self.assertEqual(wrong.status_code, 403)
            ok = client.post("/api/auth/change-email", json={"new_email": target, "password": TEST_PASSWORD})
            self.assertEqual(ok.status_code, 202, ok.text)
            self.assertIn(target, last_email(user.email).body)  # notice to the old address
            token = link_token(last_email(target))
            self.assertEqual(client.post("/api/auth/confirm-email", json={"token": token}).status_code, 200)
            self.assertEqual(client.get("/api/auth/me").json()["user"]["email"], target)
        self.assertIsNone(auth_service.get_user_by_email(user.email))

    def test_changing_to_a_taken_email_does_not_reveal_it(self):
        user, other = make_user(), make_user()
        with browser() as client:
            self.login(client, user.email)
            response = client.post("/api/auth/change-email", json={"new_email": other.email, "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 202)
        self.assertIn("já está cadastrado", last_email(other.email).body)
        self.assertEqual(auth_service.get_user_by_email(user.email).id, user.id)


class CsrfTests(AuthTestCase):
    def test_state_changes_need_a_matching_token(self):
        user = make_user()
        with TestClient(app) as client:
            client.get("/api/auth/config")
            missing = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
            self.assertEqual((missing.status_code, missing.json()["detail"]["code"]), (403, "csrf_failed"))
            mismatched = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD}, headers={"X-CSRF-Token": "other"})
            self.assertEqual(mismatched.status_code, 403)
            ok = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD},
                             headers={"X-CSRF-Token": client.cookies["csrf_token"]})
            self.assertEqual(ok.status_code, 200)
            # Reads don't need it; writes still do once signed in.
            self.assertEqual(client.get("/api/sessions").status_code, 200)
            self.assertEqual(client.post("/api/auth/logout").status_code, 403)

    def test_csrf_cookie_is_readable_by_scripts(self):
        with TestClient(app) as client:
            response = client.get("/login")
        cookie = next(value for value in response.headers.get_list("set-cookie") if value.startswith("csrf_token="))
        self.assertNotIn("HttpOnly", cookie)
        self.assertIn("samesite=lax", cookie.lower())


class RateLimitTests(AuthTestCase):
    def test_repeated_failures_on_one_account_slow_down_without_locking(self):
        user = make_user()
        with browser() as client:
            for _ in range(5):
                self.login(client, user.email, "wrong password here")
            delayed = self.login(client, user.email)
        self.assertEqual(delayed.status_code, 429)
        self.assertGreater(int(delayed.headers["retry-after"]), 0)
        with patch("app.services.rate_limit.time.time", return_value=__import__("time").time() + 60), browser() as client:
            self.assertEqual(self.login(client, user.email).status_code, 200)

    def test_signup_and_forgot_password_limits(self):
        with self.signup_open(), browser() as client:
            for _ in range(LIMITS["signup_ip"][0]):
                client.post("/api/auth/signup", json={"email": new_email(), "password": TEST_PASSWORD})
            self.assertEqual(client.post("/api/auth/signup", json={"email": new_email(), "password": TEST_PASSWORD}).status_code, 429)

            email = make_user().email
            for _ in range(LIMITS["forgot_email"][0]):
                client.post("/api/auth/forgot-password", json={"email": email})
            self.assertEqual(client.post("/api/auth/forgot-password", json={"email": email}).status_code, 429)


class TurnstileTests(AuthTestCase):
    def enabled(self, result: bool):
        return patch.multiple(turnstile, enabled=lambda: True, site_key=lambda: "site-key", verify=lambda token, ip=None: result and token == "good")

    def test_signup_and_forgot_require_a_valid_token(self):
        with self.signup_open(), self.enabled(True), browser() as client:
            self.assertEqual(client.get("/api/auth/config").json()["turnstile_site_key"], "site-key")
            rejected = client.post("/api/auth/signup", json={"email": new_email(), "password": TEST_PASSWORD, "turnstile_token": "bad"})
            self.assertEqual((rejected.status_code, rejected.json()["detail"]["code"]), (400, "turnstile_failed"))
            accepted = client.post("/api/auth/signup", json={"email": new_email(), "password": TEST_PASSWORD, "turnstile_token": "good"})
            self.assertEqual(accepted.status_code, 202)
            self.assertEqual(client.post("/api/auth/forgot-password", json={"email": new_email()}).status_code, 400)

    def test_login_requires_it_only_after_a_failure(self):
        user = make_user()
        with self.enabled(True), browser() as client:
            self.assertEqual(self.login(client, user.email, "wrong password here").status_code, 401)
            self.assertEqual(self.login(client, user.email).status_code, 400)
            self.assertEqual(self.login(client, user.email, turnstile_token="good").status_code, 200)

    def test_verifier_posts_the_secret_and_reads_success(self):
        class Reply:
            def __init__(self, success):
                self.success = success

            def json(self):
                return {"success": self.success}

        with patch.dict("os.environ", {"TURNSTILE_SITE_KEY": "s", "TURNSTILE_SECRET_KEY": "k"}):
            with patch.object(turnstile.httpx, "post", return_value=Reply(True)) as post:
                self.assertTrue(turnstile.verify("token", "203.0.113.5"))
            self.assertEqual(post.call_args.kwargs["data"], {"secret": "k", "response": "token", "remoteip": "203.0.113.5"})
            with patch.object(turnstile.httpx, "post", return_value=Reply(False)):
                self.assertFalse(turnstile.verify("token"))
            self.assertFalse(turnstile.verify(""))


class HousekeepingAndCliTests(AuthTestCase):
    def test_purge_removes_old_unverified_accounts_and_dead_sessions(self):
        old = make_user(verified=False)
        recent = make_user(verified=False)
        verified = make_user()
        ended = auth_service.start_session(verified)
        auth_service.revoke_session(ended)
        with db.transaction() as tx:
            tx.execute(update(User).where(User.id.in_([old.id, verified.id])).values(created_at=datetime.now(timezone.utc) - timedelta(days=8)))
            tx.execute(update(AuthSession).where(AuthSession.user_id == verified.id).values(revoked_at=datetime.now(timezone.utc) - timedelta(days=2)))
        result = auth_service.purge_expired()
        self.assertGreaterEqual(result["unverified_accounts"], 1)
        self.assertIsNone(auth_service.get_user(old.id))
        self.assertIsNotNone(auth_service.get_user(recent.id))
        self.assertIsNotNone(auth_service.get_user(verified.id))
        self.assertGreaterEqual(result["sessions"], 1)

    def test_create_admin_and_set_password(self):
        email = new_email()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli_main(["create-admin", "--email", email]), 0)
        password = re.search(r"Password for \S+: (\S+)", out.getvalue()).group(1)
        admin = auth_service.authenticate(email, password)
        self.assertEqual((admin.role, admin.email_verified_at is not None), ("admin", True))

        session = auth_service.start_session(admin)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli_main(["set-password", "--email", email]), 0)
        new_password = re.search(r"Password for \S+: (\S+)", out.getvalue()).group(1)
        self.assertIsNone(auth_service.authenticate(email, password))
        self.assertIsNotNone(auth_service.authenticate(email, new_password))
        self.assertIsNone(auth_service.resolve_session(session))

    def test_create_admin_promotes_an_existing_account(self):
        user = make_user()
        with redirect_stdout(io.StringIO()):
            cli_main(["create-admin", "--email", user.email])
        self.assertEqual(auth_service.get_user(user.id).role, "admin")


class PageTests(unittest.TestCase):
    def test_signed_out_visitors_are_sent_to_login_by_the_server(self):
        with TestClient(app, follow_redirects=False) as client:
            for path, target in (("/", "/login?next=%2F"), ("/templates", "/login?next=%2Ftemplates"),
                                 ("/account", "/login?next=%2Faccount"), ("/session/abc?x=1", "/login?next=%2Fsession%2Fabc%3Fx%3D1")):
                response = client.get(path)
                self.assertEqual((response.status_code, response.headers["location"]), (303, target), path)
            stale = client.get("/", headers={"Cookie": "pdf_report_session=old-hmac-token"})
            self.assertEqual(stale.status_code, 303)
            self.assertTrue(any("pdf_report_session=" in value for value in stale.headers.get_list("set-cookie")))

    def test_signed_in_users_skip_the_login_page(self):
        with TestClient(app, follow_redirects=False) as client:
            headers = signed_in_headers()
            self.assertEqual(client.get("/", headers=headers).status_code, 200)
            response = client.get("/login", headers=headers)
            self.assertEqual((response.status_code, response.headers["location"]), (303, "/"))
            # Email links work whether or not you're signed in.
            self.assertEqual(client.get("/verify-email", headers=headers).status_code, 200)

    def test_assets_are_versioned_so_old_copies_are_never_used(self):
        with TestClient(app) as client:
            page = client.get("/login")
            self.assertEqual(page.headers["cache-control"], "no-cache")
            scripts = re.findall(r'src="(/static/js/[\w.]+\?v=[0-9a-f]{12})"', page.text)
            self.assertEqual(len(scripts), 2, page.text)
            self.assertRegex(page.text, r'href="/static/css/styles\.css\?v=[0-9a-f]{12}"')
            self.assertIn("immutable", client.get(scripts[0]).headers["cache-control"])
            self.assertEqual(client.get("/static/js/api.js").headers["cache-control"], "no-cache")

    def test_auth_pages_are_served(self):
        with TestClient(app) as client:
            for path in ("/login", "/signup", "/forgot-password", "/reset-password", "/verify-email", "/confirm-email"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertIn('src="/static/js/auth.js?v=', response.text)
            self.assertIn('src="/static/js/account.js?v=', client.get("/account", headers=signed_in_headers()).text)

    def test_turnstile_is_allowed_by_the_csp_but_inline_scripts_are_not(self):
        with TestClient(app) as client:
            policy = client.get("/login").headers["content-security-policy"]
        script_src = next(part for part in policy.split(";") if part.strip().startswith("script-src"))
        self.assertIn("https://challenges.cloudflare.com", script_src)
        self.assertNotIn("unsafe-inline", script_src)
        self.assertIn("https://challenges.cloudflare.com", next(part for part in policy.split(";") if part.strip().startswith("frame-src")))


if __name__ == "__main__":
    unittest.main()
