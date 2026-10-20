"""Sign-up, sign-in and account routes (`/api/auth/*`) and the account pages."""
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.pages import render_page
from app.security import (
    clear_session_cookie,
    client_key,
    current_auth_session,
    current_user,
    set_session_cookie,
    signup_open,
    SESSION_COOKIE,
)
from app.services import auth_service, turnstile
from app.services.auth_service import AuthError, user_payload
from app.services.rate_limit import ACCOUNT_FAILURE_WINDOW_SECONDS, LIMITS, limiter

router = APIRouter()

CHECK_EMAIL_MESSAGE = "Se o email puder ser usado, você receberá uma mensagem com as instruções em instantes."


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1000)
    turnstile_token: str | None = Field(default=None, max_length=2048)


class SignupBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1000)
    display_name: str = Field(default="", max_length=100)
    turnstile_token: str | None = Field(default=None, max_length=2048)


class EmailBody(BaseModel):
    email: str = Field(max_length=320)
    turnstile_token: str | None = Field(default=None, max_length=2048)


class TokenBody(BaseModel):
    token: str = Field(max_length=200)


class ResetBody(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=1000)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(max_length=1000)
    new_password: str = Field(max_length=1000)


class ChangeEmailBody(BaseModel):
    new_email: str = Field(max_length=320)
    password: str = Field(max_length=1000)


class ProfileBody(BaseModel):
    display_name: str = Field(default="", max_length=100)


def _error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _too_many(wait: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": "rate_limited", "message": "Muitas tentativas. Aguarde alguns minutos e tente novamente."},
        headers={"Retry-After": str(max(1, wait))},
    )


def _limit(bucket: str, key: str) -> None:
    wait = limiter.check_and_hit(bucket, key)
    if wait:
        raise _too_many(wait)


def _require_turnstile(token: str | None, ip: str) -> None:
    if not turnstile.verify(token, ip):
        raise HTTPException(status_code=400, detail={
            "code": "turnstile_failed", "message": "Não foi possível confirmar que você não é um robô. Tente novamente.",
        })


def _session_max_age() -> int:
    return int(auth_service.SESSION_MAX.total_seconds())


# --- Public ---------------------------------------------------------------------------

@router.get("/api/auth/config")
def auth_config():
    return {
        "signup_open": signup_open(),
        "turnstile_site_key": turnstile.site_key() if turnstile.enabled() else "",
        "password_min_length": auth_service.PASSWORD_MIN_LENGTH,
    }


@router.post("/api/auth/login")
def login(request: Request, response: Response, body: LoginBody):
    ip = client_key(request)
    email_key = body.email.strip().lower()
    wait = max(limiter.retry_after("login_ip", ip), limiter.account_delay(email_key))
    if wait:
        raise _too_many(wait)
    # Turnstile is required from the first failed attempt on.
    recent_failures = (
        limiter.recent_count("login_ip", ip, LIMITS["login_ip"][1])
        + limiter.recent_count("login_account", email_key, ACCOUNT_FAILURE_WINDOW_SECONDS)
    )
    if recent_failures:
        _require_turnstile(body.turnstile_token, ip)

    user = auth_service.authenticate(body.email, body.password)
    if user is None:
        limiter.hit("login_ip", ip)
        limiter.hit("login_account", email_key)
        known = auth_service.get_user_by_email(body.email)
        auth_service.audit("login_failed", known.id if known else None, ip)
        raise HTTPException(status_code=401, detail={"code": "invalid_credentials", "message": "Email ou senha incorretos."})

    limiter.reset("login_account", email_key)
    auth_service.revoke_session(request.cookies.get(SESSION_COOKIE))
    token = auth_service.start_session(user, ip, request.headers.get("user-agent", ""))
    set_session_cookie(response, token, _session_max_age())
    return {"user": user_payload(user)}


@router.post("/api/auth/signup", status_code=202)
def signup(request: Request, body: SignupBody):
    if not signup_open():
        raise HTTPException(status_code=403, detail={"code": "signup_closed", "message": "Novos cadastros estão fechados no momento."})
    ip = client_key(request)
    _limit("signup_ip", ip)
    _require_turnstile(body.turnstile_token, ip)
    try:
        auth_service.signup(body.email, body.password, body.display_name, ip)
    except AuthError as exc:
        raise _error(exc) from exc
    return {"status": "OK", "message": "Conta criada. Enviamos um link de confirmação para o seu email; depois é só entrar."}


@router.post("/api/auth/verify-email")
def verify_email(request: Request, body: TokenBody):
    ip = client_key(request)
    _limit("token_ip", ip)
    try:
        user = auth_service.verify_email(body.token, ip)
    except AuthError as exc:
        raise _error(exc) from exc
    return {"status": "OK", "email": user.email}


@router.post("/api/auth/forgot-password", status_code=202)
def forgot_password(request: Request, body: EmailBody):
    ip = client_key(request)
    _limit("forgot_ip", ip)
    _limit("forgot_email", body.email.strip().lower())
    _require_turnstile(body.turnstile_token, ip)
    auth_service.request_password_reset(body.email, ip)
    return {"status": "OK", "message": CHECK_EMAIL_MESSAGE}


@router.post("/api/auth/reset-password")
def reset_password(request: Request, response: Response, body: ResetBody):
    ip = client_key(request)
    _limit("token_ip", ip)
    try:
        auth_service.reset_password(body.token, body.password, ip)
    except AuthError as exc:
        raise _error(exc) from exc
    clear_session_cookie(response)
    return {"status": "OK", "message": "Senha redefinida. Entre com a nova senha."}


@router.post("/api/auth/confirm-email")
def confirm_email(request: Request, body: TokenBody):
    ip = client_key(request)
    _limit("token_ip", ip)
    try:
        user = auth_service.confirm_email_change(body.token, ip)
    except AuthError as exc:
        raise _error(exc) from exc
    return {"status": "OK", "email": user.email}


# --- Signed in --------------------------------------------------------------------------

@router.get("/api/auth/me")
def me(request: Request):
    return {"user": user_payload(current_user(request))}


@router.patch("/api/auth/me")
def update_me(request: Request, body: ProfileBody):
    return {"user": user_payload(auth_service.update_profile(current_user(request), body.display_name))}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth_service.revoke_session(request.cookies.get(SESSION_COOKIE))
    clear_session_cookie(response)
    return {"status": "OK"}


@router.post("/api/auth/logout-all")
def logout_all(request: Request, response: Response):
    auth_service.revoke_all_sessions(current_user(request).id, ip=client_key(request))
    clear_session_cookie(response)
    return {"status": "OK"}


@router.get("/api/auth/sessions")
def list_sessions(request: Request):
    user = current_user(request)
    current = current_auth_session(request)
    return {"sessions": [
        {
            "id": str(item.id),
            "created_at": item.created_at.isoformat(),
            "last_seen_at": item.last_seen_at.isoformat(),
            "ip": str(item.ip) if item.ip else "",
            "user_agent": item.user_agent,
            "current": current is not None and item.id == current.id,
        }
        for item in auth_service.list_sessions(user.id)
    ]}


@router.delete("/api/auth/sessions/{session_id}")
def revoke_session(request: Request, session_id: str):
    try:
        parsed = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    if not auth_service.revoke_session_by_id(current_user(request).id, parsed):
        raise HTTPException(status_code=404)
    return {"status": "OK"}


@router.post("/api/auth/resend-verification", status_code=202)
def resend_verification(request: Request):
    user = current_user(request)
    if user.email_verified_at is not None:
        return {"status": "OK", "message": "Seu email já está confirmado."}
    _limit("resend_user", str(user.id))
    auth_service.send_verification(user)
    return {"status": "OK", "message": "Enviamos um novo link de confirmação."}


@router.post("/api/auth/change-password")
def change_password(request: Request, body: ChangePasswordBody):
    user = current_user(request)
    session = current_auth_session(request)
    try:
        auth_service.change_password(
            user, body.current_password, body.new_password,
            keep_session_id=session.id if session else None, ip=client_key(request),
        )
    except AuthError as exc:
        raise _error(exc) from exc
    return {"status": "OK", "message": "Senha alterada. As outras sessões foram encerradas."}


@router.post("/api/auth/change-email", status_code=202)
def change_email(request: Request, body: ChangeEmailBody):
    user = current_user(request)
    _limit("email_change_user", str(user.id))
    try:
        auth_service.request_email_change(user, body.password, body.new_email, ip=client_key(request))
    except AuthError as exc:
        raise _error(exc) from exc
    return {"status": "OK", "message": "Enviamos um link de confirmação para o novo email."}


class DeleteAccountBody(BaseModel):
    password: str = Field(max_length=1000)


@router.delete("/api/auth/account")
def delete_account(request: Request, response: Response, body: DeleteAccountBody):
    try:
        removed = auth_service.delete_account(current_user(request), body.password, ip=client_key(request))
    except AuthError as exc:
        raise _error(exc) from exc
    clear_session_cookie(response)
    return {"status": "OK", "reports_deleted": removed}


# --- Pages ------------------------------------------------------------------------------

for _path in ("/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/confirm-email"):
    router.add_api_route(_path, lambda: render_page("auth.html"), methods=["GET"], response_class=HTMLResponse, include_in_schema=False)
router.add_api_route("/account", lambda: render_page("account.html"), methods=["GET"], response_class=HTMLResponse, include_in_schema=False)
