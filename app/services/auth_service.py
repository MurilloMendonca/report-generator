"""Accounts, sign-in sessions and email-token flows (plan section 5).

Sessions: the browser holds a random token in an HttpOnly cookie; the database
stores only its SHA-256. A session ends after PDF_REPORT_SESSION_IDLE_DAYS
without use or PDF_REPORT_SESSION_MAX_DAYS after sign-in, whichever is first.

Email links (verify, reset, change email) carry a random token stored the same
way. Each is single use, and issuing a new one cancels older ones of the same
kind for that account.
"""
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import and_, delete, exists, func, or_, select, update

from app import db
from app.models.db import AuditEvent, AuthSession, EmailToken, Report, User
from app.services import email_service

logger = logging.getLogger(__name__)

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
SESSION_IDLE = timedelta(days=int(os.getenv("PDF_REPORT_SESSION_IDLE_DAYS", "7")))
SESSION_MAX = timedelta(days=int(os.getenv("PDF_REPORT_SESSION_MAX_DAYS", "30")))
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
TOKEN_LIFETIMES = {
    "verify_email": timedelta(hours=24),
    "reset_password": timedelta(minutes=30),
    "change_email": timedelta(hours=24),
}
UNVERIFIED_ACCOUNT_LIFETIME = timedelta(days=7)

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# Verified against when an email is unknown, so both cases take the same time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class AuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class SignedIn:
    user: User
    session: AuthSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _new_token() -> tuple[str, bytes]:
    raw = secrets.token_urlsafe(32)
    return raw, _hash_token(raw)


def _ip(value: str | None) -> str | None:
    import ipaddress

    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


# --- Validation -------------------------------------------------------------

def normalize_email(email: str) -> str:
    """A syntactically valid, lowercased address. Raises AuthError otherwise."""
    try:
        result = validate_email((email or "").strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise AuthError("invalid_email", "Email inválido.") from exc
    return result.normalized.lower()


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError("weak_password", f"A senha precisa ter pelo menos {PASSWORD_MIN_LENGTH} caracteres.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise AuthError("weak_password", f"A senha pode ter no máximo {PASSWORD_MAX_LENGTH} caracteres.")
    return password


def hash_password(password: str) -> str:
    return _hasher.hash(validate_password(password))


def _password_matches(stored_hash: str | None, password: str) -> bool:
    if not isinstance(password, str) or len(password) > PASSWORD_MAX_LENGTH:
        password = ""
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


# --- Audit --------------------------------------------------------------------

def audit(event: str, user_id=None, ip: str | None = None, tx=None, **details) -> None:
    row = AuditEvent(user_id=user_id, event=event, ip=_ip(ip), details=details)
    if tx is not None:
        tx.add(row)
        return
    with db.transaction() as own:
        own.add(row)


# --- Accounts -----------------------------------------------------------------

def get_user(user_id) -> User | None:
    with db.transaction() as tx:
        return tx.get(User, user_id)


def get_user_by_email(email: str) -> User | None:
    try:
        email = normalize_email(email)
    except AuthError:
        return None
    with db.transaction() as tx:
        return tx.execute(select(User).where(User.email == email)).scalar_one_or_none()


def create_user(
    email: str, password: str | None, *, display_name: str = "", role: str = "user",
    verified: bool = False, is_demo: bool = False,
) -> User:
    email = normalize_email(email)
    password_hash = hash_password(password) if password is not None else None
    with db.transaction() as tx:
        if tx.execute(select(User.id).where(User.email == email)).first():
            raise AuthError("email_taken", "Já existe uma conta com este email.", 409)
        user = User(
            email=email, password_hash=password_hash, display_name=(display_name or "").strip()[:100],
            role=role, is_demo=is_demo, email_verified_at=_now() if verified else None,
        )
        tx.add(user)
        tx.flush()
        audit("account_created", user.id, tx=tx, role=role, via="cli" if verified else "signup")
        return user


def set_password(user_id, password: str) -> None:
    """Set a password and sign the account out everywhere (CLI and reset)."""
    password_hash = hash_password(password)
    with db.transaction() as tx:
        tx.execute(update(User).where(User.id == user_id).values(password_hash=password_hash, updated_at=func.now()))
        _revoke_all(tx, user_id)


def set_role(user_id, role: str) -> None:
    with db.transaction() as tx:
        tx.execute(update(User).where(User.id == user_id).values(role=role, updated_at=func.now()))
        audit("role_changed", user_id, tx=tx, role=role)


def update_profile(user: User, display_name: str) -> User:
    with db.transaction() as tx:
        row = tx.get(User, user.id)
        row.display_name = (display_name or "").strip()[:100]
        row.updated_at = _now()
        return row


def signup(email: str, password: str, display_name: str = "", ip: str | None = None) -> None:
    """Create an unverified account and email a verification link.

    The response must not reveal whether the address was taken, so an existing
    address gets an email instead of an error."""
    email = normalize_email(email)
    validate_password(password)
    existing = get_user_by_email(email)
    if existing is not None:
        email_service.send(existing.email, "signup_existing", link=email_service.public_url("/forgot-password"))
        audit("signup_existing_email", existing.id, ip)
        return
    try:
        user = create_user(email, password, display_name=display_name)
    except AuthError as exc:
        if exc.code == "email_taken":  # lost a race with a concurrent sign-up
            return
        raise
    audit("signup", user.id, ip)
    send_verification(user)


def authenticate(email: str, password: str) -> User | None:
    """The active account with this email and password, or None. Takes the
    same time whether or not the email exists."""
    user = get_user_by_email(email)
    if not _password_matches(user.password_hash if user else None, password) or user.status != "active":
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        with db.transaction() as tx:
            tx.execute(update(User).where(User.id == user.id).values(password_hash=_hasher.hash(password)))
    return user


# --- Sessions -------------------------------------------------------------------

def start_session(user: User, ip: str | None = None, user_agent: str = "") -> str:
    """Create a session and return the raw cookie token (always a new one: no fixation)."""
    raw, token_hash = _new_token()
    now = _now()
    with db.transaction() as tx:
        tx.add(AuthSession(
            user_id=user.id, token_hash=token_hash, created_at=now, last_seen_at=now,
            idle_expires_at=now + SESSION_IDLE, absolute_expires_at=now + SESSION_MAX,
            ip=_ip(ip), user_agent=(user_agent or "")[:300],
        ))
        tx.execute(update(User).where(User.id == user.id).values(last_login_at=now))
        audit("login", user.id, ip, tx=tx)
    return raw


def resolve_session(raw: str | None) -> SignedIn | None:
    """The signed-in user for a cookie token, extending the idle expiry as it's used."""
    if not raw or len(raw) > 200:
        return None
    now = _now()
    with db.transaction() as tx:
        row = tx.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == _hash_token(raw),
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now,
                AuthSession.absolute_expires_at > now,
                User.status == "active",
            )
        ).first()
        if row is None:
            return None
        session, user = row
        if now - session.last_seen_at >= SESSION_TOUCH_INTERVAL:
            session.last_seen_at = now
            session.idle_expires_at = min(now + SESSION_IDLE, session.absolute_expires_at)
        return SignedIn(user=user, session=session)


def revoke_session(raw: str | None) -> None:
    if not raw:
        return
    with db.transaction() as tx:
        tx.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == _hash_token(raw), AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )


def revoke_session_by_id(user_id, session_id) -> bool:
    with db.transaction() as tx:
        result = tx.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        return result.rowcount > 0


def _revoke_all(tx, user_id, keep_session_id=None) -> None:
    query = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep_session_id is not None:
        query = query.where(AuthSession.id != keep_session_id)
    tx.execute(query.values(revoked_at=_now()))


def revoke_all_sessions(user_id, keep_session_id=None, ip: str | None = None) -> None:
    with db.transaction() as tx:
        _revoke_all(tx, user_id, keep_session_id)
        audit("logout_all", user_id, ip, tx=tx)


def list_sessions(user_id) -> list[AuthSession]:
    now = _now()
    with db.transaction() as tx:
        return list(tx.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now, AuthSession.absolute_expires_at > now,
            )
            .order_by(AuthSession.last_seen_at.desc())
        ).scalars())


# --- Email tokens ------------------------------------------------------------------

def _issue_token(tx, user_id, purpose: str, new_email: str | None = None) -> str:
    raw, token_hash = _new_token()
    now = _now()
    tx.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user_id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    tx.add(EmailToken(
        user_id=user_id, purpose=purpose, token_hash=token_hash, new_email=new_email,
        expires_at=now + TOKEN_LIFETIMES[purpose],
    ))
    return raw


def _consume_token(tx, raw: str, purpose: str) -> EmailToken:
    invalid = AuthError("invalid_token", "Este link é inválido ou expirou. Peça um novo.")
    if not raw or len(raw) > 200:
        raise invalid
    token = tx.execute(
        select(EmailToken).where(EmailToken.token_hash == _hash_token(raw), EmailToken.purpose == purpose).with_for_update()
    ).scalar_one_or_none()
    if token is None or token.used_at is not None or token.expires_at <= _now():
        raise invalid
    token.used_at = _now()
    return token


def send_verification(user: User) -> None:
    if user.email_verified_at is not None:
        return
    with db.transaction() as tx:
        raw = _issue_token(tx, user.id, "verify_email")
    greeting = f", {user.display_name}" if user.display_name else ""
    email_service.send(user.email, "verify_email", greeting=greeting, link=email_service.public_url(f"/verify-email#token={raw}"))


def verify_email(raw: str, ip: str | None = None) -> User:
    with db.transaction() as tx:
        token = _consume_token(tx, raw, "verify_email")
        user = tx.get(User, token.user_id)
        if user.email_verified_at is None:
            user.email_verified_at = _now()
            user.updated_at = _now()
        audit("email_verified", user.id, ip, tx=tx)
        return user


def request_password_reset(email: str, ip: str | None = None) -> None:
    """Email a reset link if the account exists. Says nothing either way."""
    user = get_user_by_email(email)
    if user is None or user.status != "active":
        return
    with db.transaction() as tx:
        raw = _issue_token(tx, user.id, "reset_password")
        audit("password_reset_requested", user.id, ip, tx=tx)
    email_service.send(user.email, "reset_password", link=email_service.public_url(f"/reset-password#token={raw}"))


def reset_password(raw: str, password: str, ip: str | None = None) -> User:
    password_hash = hash_password(password)
    with db.transaction() as tx:
        token = _consume_token(tx, raw, "reset_password")
        user = tx.get(User, token.user_id)
        user.password_hash = password_hash
        user.updated_at = _now()
        # The link proves the address belongs to the user.
        user.email_verified_at = user.email_verified_at or _now()
        _revoke_all(tx, user.id)
        audit("password_reset", user.id, ip, tx=tx)
        return user


def change_password(user: User, current: str, new: str, keep_session_id=None, ip: str | None = None) -> None:
    with db.transaction() as tx:
        row = tx.get(User, user.id, with_for_update=True)
        if not _password_matches(row.password_hash, current):
            raise AuthError("wrong_password", "Senha atual incorreta.", 403)
        row.password_hash = hash_password(new)
        row.updated_at = _now()
        _revoke_all(tx, user.id, keep_session_id)
        audit("password_changed", user.id, ip, tx=tx)
    email_service.send(user.email, "password_changed", link=email_service.public_url("/forgot-password"))


def request_email_change(user: User, password: str, new_email: str, ip: str | None = None) -> None:
    new_email = normalize_email(new_email)
    if new_email == user.email:
        raise AuthError("same_email", "Este já é o email da sua conta.")
    with db.transaction() as tx:
        row = tx.get(User, user.id)
        if not _password_matches(row.password_hash, password):
            raise AuthError("wrong_password", "Senha atual incorreta.", 403)
        taken = tx.execute(select(User.id).where(User.email == new_email)).first() is not None
        raw = None if taken else _issue_token(tx, user.id, "change_email", new_email)
        audit("email_change_requested", user.id, ip, tx=tx)
    if raw is None:
        # Don't reveal that the address has an account; tell its owner instead.
        email_service.send(new_email, "signup_existing", link=email_service.public_url("/forgot-password"))
    else:
        email_service.send(new_email, "change_email_confirm", link=email_service.public_url(f"/confirm-email#token={raw}"))
    email_service.send(user.email, "change_email_notice", new_email=new_email, link=email_service.public_url("/account"))


def confirm_email_change(raw: str, ip: str | None = None) -> User:
    with db.transaction() as tx:
        token = _consume_token(tx, raw, "change_email")
        if tx.execute(select(User.id).where(User.email == token.new_email)).first() is not None:
            raise AuthError("email_taken", "Este email passou a ser usado por outra conta.", 409)
        user = tx.get(User, token.user_id)
        old_email, user.email = user.email, token.new_email
        user.email_verified_at = _now()
        user.updated_at = _now()
        audit("email_changed", user.id, ip, tx=tx, old_email=old_email)
        return user


# --- Housekeeping --------------------------------------------------------------------

def purge_expired() -> dict:
    """Drop ended sessions, used or expired email tokens, and unverified accounts
    older than a week that own nothing."""
    now = _now()
    with db.transaction() as tx:
        sessions = tx.execute(delete(AuthSession).where(or_(
            AuthSession.absolute_expires_at < now,
            AuthSession.idle_expires_at < now,
            AuthSession.revoked_at < now - timedelta(days=1),
        ))).rowcount
        tokens = tx.execute(delete(EmailToken).where(or_(
            EmailToken.expires_at < now, EmailToken.used_at.is_not(None),
        ))).rowcount
        accounts = tx.execute(delete(User).where(and_(
            User.email_verified_at.is_(None),
            User.created_at < now - UNVERIFIED_ACCOUNT_LIFETIME,
            User.role != "admin",
            ~exists().where(Report.owner_id == User.id),
        ))).rowcount
    return {"sessions": sessions, "email_tokens": tokens, "unverified_accounts": accounts}


def user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "email_verified": user.email_verified_at is not None,
        "is_demo": user.is_demo,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }

