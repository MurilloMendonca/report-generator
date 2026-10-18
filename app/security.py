"""Access control: shared-secret login, signed session tokens, revocation and headers."""
import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import socket
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from app.models.db import RevokedToken

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
    """Nonces of logged-out tokens, kept until the token would have expired anyway.
    Stored in Postgres so every process and restart sees them. Phase 2 replaces
    the signed tokens with server-side sessions and drops this table."""

    def revoke(self, nonce: str, expires_at: float) -> None:
        expires = datetime.fromtimestamp(expires_at, timezone.utc)
        try:
            with db.transaction() as tx:
                tx.execute(delete(RevokedToken).where(RevokedToken.expires_at <= func.now()))
                statement = pg_insert(RevokedToken).values(nonce=nonce, expires_at=expires)
                tx.execute(statement.on_conflict_do_update(index_elements=[RevokedToken.nonce], set_={"expires_at": expires}))
        except Exception:
            logger.exception("Could not persist revoked session token")

    def is_revoked(self, nonce: str) -> bool:
        with db.transaction() as tx:
            return tx.execute(
                select(RevokedToken.nonce).where(RevokedToken.nonce == nonce, RevokedToken.expires_at > func.now())
            ).first() is not None

    def reset(self) -> None:
        """Nothing is cached in memory; kept so callers can simulate a restart."""


revoked_tokens = RevokedTokenStore()


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


CLIENT_IP_HEADER = os.getenv("PDF_REPORT_CLIENT_IP_HEADER", "").strip()
TRUSTED_PROXIES = [entry.strip() for entry in os.getenv("PDF_REPORT_TRUSTED_PROXIES", "").split(",") if entry.strip()]
_HOST_CACHE_SECONDS = 60
_resolved_hosts: dict[str, tuple[float, set[str]]] = {}


def _resolve_host(hostname: str) -> set[str]:
    cached = _resolved_hosts.get(hostname)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except OSError:
        addresses = set()
    _resolved_hosts[hostname] = (time.time() + _HOST_CACHE_SECONDS, addresses)
    return addresses


def _is_trusted_proxy(peer: str, trusted: list[str]) -> bool:
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if peer_ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            # Not an address or network: a hostname such as the cloudflared service.
            if peer in _resolve_host(entry):
                return True
    return False


def client_key(request: Request | None) -> str:
    """The client's address, taken from the proxy header only when a trusted proxy sent it.

    Behind the Cloudflare Tunnel every request comes from cloudflared, so the real
    address is in CF-Connecting-IP; trusting that header from anyone else would let
    clients pick their own address and dodge rate limits.
    """
    client = getattr(request, "client", None)
    peer = getattr(client, "host", None) or ""
    if CLIENT_IP_HEADER and peer and _is_trusted_proxy(peer, TRUSTED_PROXIES):
        forwarded = str(getattr(request, "headers", {}).get(CLIENT_IP_HEADER, "")).split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer or "unknown"


def require_admin(request: Request) -> None:
    """Admin endpoints need APP_ADMIN_SECRET; without it they only work in development."""
    if APP_ADMIN_SECRET:
        supplied = request.headers.get("x-admin-secret", "")
        if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), APP_ADMIN_SECRET.encode("utf-8")):
            raise HTTPException(status_code=403, detail="Admin secret required")
        return
    if not IS_DEV_ENV:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled; configure APP_ADMIN_SECRET")


PRIVATE_PATH_PREFIXES = ("/api/", "/session/")


def add_cache_headers(path: str, response: Response):
    """Keep authenticated responses out of shared caches such as Cloudflare's edge."""
    if path.startswith(PRIVATE_PATH_PREFIXES):
        response.headers.setdefault("Cache-Control", "private, no-store")


def add_security_headers(response: Response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
