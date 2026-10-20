"""Signed-in test clients: real accounts and sessions in the test database.

Most tests act as one shared default user: `signed_in_headers()` signs in as
it and `create_session()` here makes reports it owns. Pass `user=`/`owner=`,
or any make_user option, to act as someone else.
"""
import secrets
import uuid

from app.services import auth_service, session_manager
from app.security import CSRF_COOKIE, SESSION_COOKIE

TEST_PASSWORD = "correct horse battery staple"


def make_user(email: str | None = None, *, password: str = TEST_PASSWORD, verified: bool = True, role: str = "user", name: str = ""):
    email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
    return auth_service.create_user(email, password, display_name=name, role=role, verified=verified)


_default_user = None


def default_user():
    """One verified user per test run (the test database is emptied at start-up)."""
    global _default_user
    if _default_user is None or auth_service.get_user(_default_user.id) is None:
        _default_user = make_user(name="Default test user")
    return _default_user


def create_session(job_id: str, data: dict, owner=None):
    """session_manager.create_session with the default user as owner."""
    return session_manager.create_session(job_id, data, owner_id=(owner or default_user()).id)


def signed_in_headers(user=None, **user_options) -> dict:
    """Headers for a request made by `user` (the default user, or a new one when
    make_user options are given), including a matching CSRF cookie and header."""
    user = user or (make_user(**user_options) if user_options else default_user())
    token = auth_service.start_session(user, "127.0.0.1", "unittest")
    csrf = secrets.token_urlsafe(16)
    return {"Cookie": f"{SESSION_COOKIE}={token}; {CSRF_COOKIE}={csrf}", "X-CSRF-Token": csrf}


def csrf_headers() -> dict:
    """Headers for an anonymous state-changing request (login, sign-up)."""
    csrf = secrets.token_urlsafe(16)
    return {"Cookie": f"{CSRF_COOKIE}={csrf}", "X-CSRF-Token": csrf}


class RequestAs:
    """Stand-in for a FastAPI Request when a test calls a route handler directly:
    carries the signed-in user the auth middleware would have set."""

    def __init__(self, user=None):
        self.state = type("State", (), {"user": user or default_user(), "auth_session": None})()
        self.client = type("Client", (), {"host": "127.0.0.1"})()
        self.headers = {}
        self.cookies = {}


def request_as(user=None) -> RequestAs:
    return RequestAs(user)
