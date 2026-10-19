import logging
import os
import sys
from urllib.parse import quote
from contextlib import asynccontextmanager

if __package__ in (None, ""):
    # Allow `python app/main.py` from the project root.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.db import get_engine
from app.routers.api import router as api_router
from app.routers.auth import router as auth_router
from app.routers.credentials import router as credentials_router
from app.routers.operations import router as operations_router
from app.routers.sources import router as sources_router
from app.security import (
    PUBLIC_API_PATHS, SESSION_COOKIE, add_cache_headers, add_security_headers, clear_session_cookie,
    csrf_ok, ensure_csrf_cookie, signup_open,
)
from app.services import auth_service, email_service, turnstile
from app.services.crypto import validate_configuration
from app.services.redaction import install_log_redaction, redact
from app.services.executor_service import sandbox_status
from app.services.session_manager import recover_interrupted_sessions

load_dotenv()
install_log_redaction()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_configuration()
    from app.services.deployment_service import validate_runtime_configuration
    validate_runtime_configuration()
    get_engine()  # connect and migrate now, so a database problem stops startup
    logger.info("Auth housekeeping: %s", auth_service.purge_expired())
    if email_service.backend() == "console":
        logger.warning("SMTP_HOST is not set: emails (verification, password reset) are only written to the log.")
    if signup_open() and not turnstile.enabled():
        logger.warning("Sign-up is open without Turnstile (TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY).")
    # Skipped under tests: they share workspace/ with a possibly running dev server.
    if os.getenv("PDF_REPORT_RECOVER_SESSIONS_ON_START", "0" if os.getenv("APP_ENV") == "test" else "1") == "1":
        recover_interrupted_sessions()
        from app.services.usage_service import recover_pending
        recover_pending()
    status = sandbox_status()
    if not status["bubblewrap_available"]:
        if status["unsafe_fallback_allowed"]:
            logger.warning("bubblewrap unavailable (%s); generated code runs under the weaker python-guard.", status["bubblewrap_error"])
        else:
            logger.error("bubblewrap unavailable (%s); report generation will fail until it is fixed.", status["bubblewrap_error"])
    yield


app = FastAPI(title="Report Generator v0.3.6", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth_router)
app.include_router(credentials_router)
app.include_router(operations_router)
app.include_router(sources_router)
app.include_router(api_router)


# Pages that need a signed-in user (signed-out visitors go to /login), and
# pages only for signed-out visitors (signed-in users go to the studio).
SIGNED_IN_PAGES = {"/", "/templates", "/account", "/keys", "/usage", "/admin", "/sources"}
SIGNED_OUT_PAGES = {"/login", "/signup", "/forgot-password"}


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    # FastAPI normally echoes rejected input, including secrets in malformed JSON.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": redact(error["loc"]), "type": error["type"], "msg": "Valor inválido."}
        for error in exc.errors()
    ]})


def _reject(status_code: int, detail) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status_code)
    add_security_headers(response)
    add_cache_headers("/api/", response)
    return response


@app.middleware("http")
async def require_api_access(request: Request, call_next):
    """CSRF on every state-changing API call, then the session cookie on every
    non-public API path. The signed-in user ends up in request.state.user."""
    request.state.user = None
    request.state.auth_session = None
    path = request.url.path
    stale_cookie = False
    if request.method == "GET" and (path in SIGNED_IN_PAGES or path.startswith("/session/") or path in SIGNED_OUT_PAGES):
        token = request.cookies.get(SESSION_COOKIE)
        signed_in = await run_in_threadpool(auth_service.resolve_session, token) if token else None
        if signed_in is None and path not in SIGNED_OUT_PAGES:
            target = path + (f"?{request.url.query}" if request.url.query else "")
            response = RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
            if token:
                clear_session_cookie(response)
            return response
        if signed_in is not None and path in SIGNED_OUT_PAGES:
            return RedirectResponse("/", status_code=303)
        if signed_in is not None:
            request.state.user = signed_in.user
            request.state.auth_session = signed_in.session
    elif path.startswith("/api/"):
        if not csrf_ok(request):
            return _reject(403, {"code": "csrf_failed", "message": "Sessão do navegador inválida. Recarregue a página e tente novamente."})
        token = request.cookies.get(SESSION_COOKIE)
        signed_in = await run_in_threadpool(auth_service.resolve_session, token) if token else None
        if signed_in is not None:
            request.state.user = signed_in.user
            request.state.auth_session = signed_in.session
        elif path not in PUBLIC_API_PATHS:
            response = _reject(401, "Unauthorized")
            if token:
                clear_session_cookie(response)
            return response
        else:
            stale_cookie = bool(token)
    response = await call_next(request)
    if stale_cookie:
        clear_session_cookie(response)
    ensure_csrf_cookie(request, response)
    add_security_headers(response)
    add_cache_headers(path, response, request.url.query)
    if request.state.user is not None:
        response.headers["Cache-Control"] = "private, no-store"
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["app", "static"])
