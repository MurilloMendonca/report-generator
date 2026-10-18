"""Access control: shared-secret login, signed session tokens, revocation and headers."""
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque

from dotenv import load_dotenv
from fastapi import HTTPException, Request, Response

# Load .env before reading secrets; this module is imported before the LLM client.
load_dotenv()

logger = logging.getLogger(__name__)

APP_ACCESS_SECRET = os.getenv("APP_ACCESS_SECRET") or os.getenv("PDF_REPORT_ACCESS_SECRET") or "change-me"
APP_ADMIN_SECRET = os.getenv("APP_ADMIN_SECRET", "")
APP_ENV = os.getenv("APP_ENV", "development").lower()
DEV_ENVIRONMENTS = {"development", "dev", "local", "test"}
IS_DEV_ENV = APP_ENV in DEV_ENVIRONMENTS
ALLOW_DEFAULT_SECRET = IS_DEV_ENV or os.getenv("PDF_REPORT_ALLOW_DEFAULT_SECRET") == "1"
AUTH_COOKIE_NAME = "pdf_report_session"
AUTH_SESSION_TTL_SECONDS = int(os.getenv("PDF_REPORT_SESSION_TTL_SECONDS", str(8 * 60 * 60)))
AUTH_COOKIE_SECURE = os.getenv("PDF_REPORT_COOKIE_SECURE", "0" if IS_DEV_ENV else "1") == "1"
LOGIN_MAX_FAILURES = int(os.getenv("PDF_REPORT_LOGIN_MAX_FAILURES", "10"))
LOGIN_WINDOW_SECONDS = int(os.getenv("PDF_REPORT_LOGIN_WINDOW_SECONDS", "300"))
REVOKED_TOKENS_PATH = os.path.join("workspace", "auth", "revoked_tokens.json")
PUBLIC_API_PATHS = {"/api/auth/login"}

if APP_ACCESS_SECRET == "change-me" and not ALLOW_DEFAULT_SECRET:
    raise RuntimeError("APP_ACCESS_SECRET or PDF_REPORT_ACCESS_SECRET must be configured outside development mode.")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' blob: data:; "
    "frame-src 'self' blob:; "
    "connect-src 'self'"
)


def is_valid_access_token(token: str) -> bool:
    return isinstance(token, str) and bool(token) and hmac.compare_digest(token.encode("utf-8"), APP_ACCESS_SECRET.encode("utf-8"))


def _sign_session_payload(payload: str) -> str:
    return hmac.new(APP_ACCESS_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token() -> str:
    issued_at = str(int(time.time()))
    nonce = uuid.uuid4().hex
    payload = f"{issued_at}.{nonce}"
    signature = _sign_session_payload(payload)
    raw_token = f"{payload}.{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw_token).decode("ascii")


def _decode_session_token(token: str) -> tuple[int, str] | None:
    """Return (issued_at, nonce) for a correctly signed, unexpired token."""
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        issued_at, nonce, signature = decoded.split(".", 2)
        if not issued_at.isdigit() or not nonce:
            return None
        age = int(time.time()) - int(issued_at)
        if age < 0 or age > AUTH_SESSION_TTL_SECONDS:
            return None
        expected = _sign_session_payload(f"{issued_at}.{nonce}")
        if not hmac.compare_digest(signature, expected):
            return None
        return int(issued_at), nonce
    except Exception:
        return None


class RevokedTokenStore:
    """Nonces of logged-out tokens, kept until the token would have expired anyway."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._revoked: dict[str, float] | None = None
        self._mtime: float | None = None

    def _file_mtime(self) -> float | None:
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return None

    def _load(self) -> dict[str, float]:
        # Reload when another worker process has changed the file.
        mtime = self._file_mtime()
        if self._revoked is None or mtime != self._mtime:
            self._mtime = mtime
            try:
                with open(self.path, encoding="utf-8") as handle:
                    data = json.load(handle)
                self._revoked = {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._revoked = {}
        return self._revoked

    def _prune(self, revoked: dict[str, float]) -> None:
        now = time.time()
        for nonce in [nonce for nonce, expires_at in revoked.items() if expires_at <= now]:
            del revoked[nonce]

    def _save(self, revoked: dict[str, float]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(revoked, handle)
        os.replace(tmp_path, self.path)
        self._mtime = self._file_mtime()

    def revoke(self, nonce: str, expires_at: float) -> None:
        with self._lock:
            revoked = self._load()
            self._prune(revoked)
            revoked[nonce] = expires_at
            try:
                self._save(revoked)
            except OSError:
                logger.exception("Could not persist revoked session token")

    def is_revoked(self, nonce: str) -> bool:
        with self._lock:
            return nonce in self._load()

    def reset(self) -> None:
        with self._lock:
            self._revoked = None
            self._mtime = None


revoked_tokens = RevokedTokenStore(REVOKED_TOKENS_PATH)


def is_valid_session_token(token: str) -> bool:
    decoded = _decode_session_token(token)
    return decoded is not None and not revoked_tokens.is_revoked(decoded[1])


def revoke_session_token(token: str) -> bool:
    decoded = _decode_session_token(token)
    if decoded is None:
        return False
    issued_at, nonce = decoded
    revoked_tokens.revoke(nonce, issued_at + AUTH_SESSION_TTL_SECONDS)
    return True


def request_session_token(request: Request) -> str:
    return request.cookies.get(AUTH_COOKIE_NAME, "") or request.headers.get("x-access-token", "")


def request_has_valid_auth(request: Request) -> bool:
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "")
    header_token = request.headers.get("x-access-token", "")
    return is_valid_session_token(cookie_token) or is_valid_session_token(header_token)


class LoginRateLimiter:
    """Sliding-window limit on failed logins per client address."""

    def __init__(self, max_failures: int, window_seconds: int):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque:
        failures = self._failures[key]
        while failures and failures[0] <= now - self.window_seconds:
            failures.popleft()
        return failures

    def retry_after(self, key: str) -> int:
        """Seconds until the client may try again, or 0 when not blocked."""
        now = time.time()
        with self._lock:
            failures = self._recent(key, now)
            if len(failures) < self.max_failures:
                return 0
            return max(1, int(failures[0] + self.window_seconds - now) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(time.time())

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


login_rate_limiter = LoginRateLimiter(LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS)


def client_key(request: Request | None) -> str:
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def require_admin(request: Request) -> None:
    """Admin endpoints need APP_ADMIN_SECRET; without it they only work in development."""
    if APP_ADMIN_SECRET:
        supplied = request.headers.get("x-admin-secret", "")
        if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), APP_ADMIN_SECRET.encode("utf-8")):
            raise HTTPException(status_code=403, detail="Admin secret required")
        return
    if not IS_DEV_ENV:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled; configure APP_ADMIN_SECRET")


def add_security_headers(response: Response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
