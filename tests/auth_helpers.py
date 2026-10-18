"""Signed-in test clients: real accounts and sessions in the test database."""
import secrets
import uuid

from app.services import auth_service
from app.security import CSRF_COOKIE, SESSION_COOKIE

TEST_PASSWORD = "correct horse battery staple"


def make_user(email: str | None = None, *, password: str = TEST_PASSWORD, verified: bool = True, role: str = "user", name: str = ""):
    email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
    return auth_service.create_user(email, password, display_name=name, role=role, verified=verified)


def signed_in_headers(user=None, **user_options) -> dict:
    """Headers for a request made by `user` (a new verified user by default),
    including a matching CSRF cookie and header."""
    user = user or make_user(**user_options)
    token = auth_service.start_session(user, "127.0.0.1", "unittest")
    csrf = secrets.token_urlsafe(16)
    return {"Cookie": f"{SESSION_COOKIE}={token}; {CSRF_COOKIE}={csrf}", "X-CSRF-Token": csrf}


def csrf_headers() -> dict:
    """Headers for an anonymous state-changing request (login, sign-up)."""
    csrf = secrets.token_urlsafe(16)
    return {"Cookie": f"{CSRF_COOKIE}={csrf}", "X-CSRF-Token": csrf}
