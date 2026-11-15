"""Personal API tokens: bearer credentials for scripts and integrations such as the MCP server.

A token acts as its owner on report, source and template routes. It cannot manage the
account (sign-in, password, email, other tokens), provider keys or saved database
connections, and it never reaches admin routes. Only the SHA-256 of a token is stored.
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app import db
from app.models.db import ApiToken, User
from app.services.auth_service import audit

TOKEN_PREFIX = "rg_"
MAX_ACTIVE_TOKENS = 20
TOUCH_INTERVAL = timedelta(minutes=5)
# (method or None for any, path prefix) that a bearer token may not call.
BLOCKED_ROUTES = (
    (None, "/api/auth/"),
    (None, "/api/tokens"),
    (None, "/api/admin"),
    (None, "/api/credentials"),
    ("PUT", "/api/model-preferences"),
    ("POST", "/api/data-sources/connections"),
    ("PUT", "/api/data-sources/connections"),
    ("DELETE", "/api/data-sources/connections"),
)


@dataclass
class TokenUser:
    user: User
    token: ApiToken


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


def bearer_token(authorization: str | None) -> str | None:
    """The API token in an ``Authorization: Bearer rg_...`` header; other schemes are ignored."""
    scheme, _, value = (authorization or "").partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value.startswith(TOKEN_PREFIX):
        return None
    return value


def blocked_for_tokens(method: str, path: str) -> bool:
    return any((blocked_method is None or blocked_method == method) and path.startswith(prefix)
               for blocked_method, prefix in BLOCKED_ROUTES)


def summary(token: ApiToken) -> dict:
    now = _now()
    return {"id": str(token.id), "name": token.name, "hint": token.hint,
            "created_at": token.created_at.isoformat() if token.created_at else None,
            "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
            "expires_at": token.expires_at.isoformat(),
            "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
            "active": token.revoked_at is None and token.expires_at > now}


def create_token(user_id, name: str, expires_in_days: int, ip: str | None = None) -> dict:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    with db.transaction() as tx:
        active = tx.execute(select(func.count()).select_from(ApiToken).where(
            ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None), ApiToken.expires_at > now)).scalar_one()
        if active >= MAX_ACTIVE_TOKENS:
            raise HTTPException(409, detail={"code": "too_many_tokens",
                                             "message": f"Revogue um token antes de criar outro (limite de {MAX_ACTIVE_TOKENS})."})
        token = ApiToken(user_id=user_id, name=name.strip(), token_hash=_hash(raw), hint=raw[-4:],
                         created_at=now, expires_at=now + timedelta(days=expires_in_days))
        tx.add(token)
        tx.flush()
        audit("api_token_created", user_id, ip, tx=tx, token_id=str(token.id), expires_in_days=expires_in_days)
        return {**summary(token), "token": raw}


def list_tokens(user_id) -> list[dict]:
    with db.transaction() as tx:
        rows = tx.execute(select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc())).scalars().all()
        return [summary(row) for row in rows]


def revoke_token(user_id, token_id: str, ip: str | None = None) -> dict:
    try:
        token_uuid = uuid.UUID(str(token_id))
    except ValueError:
        raise HTTPException(404, detail="Token não encontrado.") from None
    with db.transaction() as tx:
        token = tx.execute(select(ApiToken).where(ApiToken.id == token_uuid, ApiToken.user_id == user_id)
                           .with_for_update()).scalar_one_or_none()
        if token is None:
            raise HTTPException(404, detail="Token não encontrado.")
        if token.revoked_at is None:
            token.revoked_at = _now()
            audit("api_token_revoked", user_id, ip, tx=tx, token_id=str(token.id))
        return summary(token)


def resolve_token(raw: str | None) -> TokenUser | None:
    """The active owner of a token, recording when it was last used."""
    if not raw or len(raw) > 200:
        return None
    now = _now()
    with db.transaction() as tx:
        row = tx.execute(
            select(ApiToken, User).join(User, User.id == ApiToken.user_id).where(
                ApiToken.token_hash == _hash(raw), ApiToken.revoked_at.is_(None),
                ApiToken.expires_at > now, User.status == "active")
        ).first()
        if row is None:
            return None
        token, user = row
        if token.last_used_at is None or now - token.last_used_at >= TOUCH_INTERVAL:
            token.last_used_at = now
        return TokenUser(user=user, token=token)
