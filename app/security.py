"""Request security: session cookies, CSRF, the signed-in user, client IPs and headers.

Accounts and sessions themselves live in app/services/auth_service.py.
"""
import hmac
import ipaddress
import os
import secrets
import socket
import time

from dotenv import load_dotenv
from fastapi import HTTPException, Request, Response

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_DEV_ENV = APP_ENV in {"development", "dev", "local", "test"}
SESSION_COOKIE = "pdf_report_session"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
COOKIE_SECURE = os.getenv("PDF_REPORT_COOKIE_SECURE", "0" if IS_DEV_ENV else "1") == "1"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Reachable without signing in. Every other /api/ path needs a session.
PUBLIC_API_PATHS = {
    "/api/auth/config",
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/verify-email",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/confirm-email",
}

TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    f"script-src 'self' {TURNSTILE_ORIGIN}; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' blob: data:; "
    f"frame-src 'self' blob: {TURNSTILE_ORIGIN}; "
    "connect-src 'self'"
)


def signup_open() -> bool:
    """PDF_REPORT_SIGNUP_MODE=open lets anyone create an account; `closed` (default)
    means accounts only come from `python -m app.cli create-user`."""
    return os.getenv("PDF_REPORT_SIGNUP_MODE", "closed").strip().lower() == "open"


# --- Cookies and CSRF ------------------------------------------------------------

def set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=max_age_seconds, httponly=True,
        secure=COOKIE_SECURE, samesite="lax", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")


def ensure_csrf_cookie(request: Request, response: Response) -> None:
    """Double-submit CSRF: a readable cookie that scripts echo in X-CSRF-Token."""
    already_set = any(value.startswith(f"{CSRF_COOKIE}=") for value in response.headers.getlist("set-cookie"))
    if not request.cookies.get(CSRF_COOKIE) and not already_set:
        response.set_cookie(
            CSRF_COOKIE, secrets.token_urlsafe(32), max_age=60 * 60 * 24 * 365,
            httponly=False, secure=COOKIE_SECURE, samesite="lax", path="/",
        )


def csrf_ok(request: Request) -> bool:
    if request.method in SAFE_METHODS:
        return True
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    return bool(cookie) and len(cookie) <= 200 and hmac.compare_digest(cookie.encode(), header.encode())


# --- The signed-in user ------------------------------------------------------------

def current_user(request: Request):
    """The signed-in user (set by the middleware in app/main.py). 401 when absent."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def current_auth_session(request: Request):
    return getattr(request.state, "auth_session", None)


def require_verified_user(request: Request):
    user = current_user(request)
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail={
            "code": "email_not_verified",
            "message": "Confirme seu email antes de criar relatórios. Verifique sua caixa de entrada.",
        })
    return user


def require_admin(request: Request):
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


# --- Client IP ------------------------------------------------------------------------

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
