"""Owner-scoped credential lifecycle. Only encrypted envelopes reach Postgres."""
import uuid
import os
from dataclasses import asdict
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from sqlalchemy import select

from app.db import transaction
from app.models.db import ProviderCredential, User
from app.services import crypto
from app.services.auth_service import audit
from app.services.llm.errors import InvalidCredentialError, MissingCredentialError
from app.services.llm.providers.registry import ADAPTERS, get_adapter
from app.services.redaction import redact
from app.utils.model_config import DEFAULT_MODEL_BY_PROVIDER, DEFAULT_PROVIDER, MODEL_CATALOG, current_model_name


class CredentialValidationError(RuntimeError):
    def __init__(self, *, invalid=False):
        self.status_code = 400 if invalid else 503
        self.code = "invalid_provider_key" if invalid else "credential_validation_unavailable"
        super().__init__("O provedor recusou esta chave." if invalid else "Não foi possível validar a chave agora. Tente novamente.")


def _now():
    return datetime.now(timezone.utc)


def aad(row) -> bytes:
    return f"{row.user_id}|{row.provider}|{row.id}".encode()


def envelope(row) -> crypto.EncryptedSecret:
    return crypto.EncryptedSecret(**{name: getattr(row, name) for name in crypto.EncryptedSecret.__dataclass_fields__})


def decrypt(row) -> str:
    try:
        return crypto.decrypt_secret(envelope(row), aad(row))
    except (InvalidTag, crypto.EncryptionConfigError, ValueError):
        raise InvalidCredentialError(row.provider) from None


def get_credential(user_id, provider) -> ProviderCredential | None:
    with transaction() as tx:
        return tx.execute(select(ProviderCredential).where(
            ProviderCredential.user_id == user_id, ProviderCredential.provider == provider,
        )).scalar_one_or_none()


def list_credentials(user_id) -> list[ProviderCredential]:
    with transaction() as tx:
        return list(tx.execute(select(ProviderCredential).where(ProviderCredential.user_id == user_id)).scalars())


def payload(row) -> dict:
    return {"provider": row.provider, "label": row.label, "key_last4": row.key_last4,
            "status": row.status, "last_error": row.last_error,
            "last_validated_at": row.last_validated_at.isoformat() if row.last_validated_at else None,
            "model_count": len(row.available_models)}


def validate_key(provider: str, key: str) -> list[str]:
    adapter = get_adapter(provider)
    try:
        # Never pass provider exception text to logs, responses or chained tracebacks.
        return sorted({redact(str(model), (key,)) for model in adapter.validate_key(key)})
    except Exception as exc:
        invalid = adapter.classify_error(exc) == "auth"
    raise CredentialValidationError(invalid=invalid) from None


def put_credential(user_id, provider: str, key: str, ip=None) -> dict:
    get_adapter(provider)
    key = key.strip()
    if not 8 <= len(key) <= 4096:
        raise CredentialValidationError(invalid=True)
    crypto.keyring()  # fail before contacting the provider if storage is unconfigured
    models = validate_key(provider, key)
    with transaction() as tx:
        # Serialize upserts, including two concurrent first inserts for this user.
        if tx.get(User, user_id, with_for_update=True) is None:
            raise MissingCredentialError(provider)
        row = tx.execute(select(ProviderCredential).where(
            ProviderCredential.user_id == user_id, ProviderCredential.provider == provider,
        ).with_for_update()).scalar_one_or_none()
        replacing = row is not None
        if row is None:
            row = ProviderCredential(id=uuid.uuid4(), user_id=user_id, provider=provider, label=ADAPTERS[provider].label)
            tx.add(row)
        for name, value in asdict(crypto.encrypt_secret(key, aad(row))).items():
            setattr(row, name, value)
        row.key_last4, row.status, row.last_error = key[-4:], "valid", None
        row.available_models, row.last_validated_at, row.updated_at = models, _now(), _now()
        audit("key_replaced" if replacing else "key_added", user_id, ip, tx=tx, provider=provider)
        return payload(row)


def mark_invalid(user_id, provider, credential_id, ciphertext):
    """A failure from an old in-flight key must never invalidate its replacement."""
    with transaction() as tx:
        row = tx.execute(select(ProviderCredential).where(
            ProviderCredential.user_id == user_id, ProviderCredential.provider == provider,
            ProviderCredential.id == credential_id,
        ).with_for_update()).scalar_one_or_none()
        if row is not None and row.ciphertext == ciphertext:
            row.status, row.last_error, row.updated_at = "invalid", "O provedor recusou esta chave (401/403).", _now()


def test_credential(user_id, provider, ip=None) -> dict:
    row = get_credential(user_id, provider)
    if row is None:
        raise MissingCredentialError(provider)
    key = decrypt(row)
    try:
        models = validate_key(provider, key)
    except CredentialValidationError as exc:
        if exc.code == "invalid_provider_key":
            mark_invalid(user_id, provider, row.id, row.ciphertext)
        raise
    with transaction() as tx:
        current = tx.get(ProviderCredential, row.id, with_for_update=True)
        if current is None or current.ciphertext != row.ciphertext:
            raise MissingCredentialError(provider)
        current.available_models, current.last_validated_at = models, _now()
        current.status, current.last_error, current.updated_at = "valid", None, _now()
        audit("key_tested", user_id, ip, tx=tx, provider=provider)
        return payload(current)


def delete_credential(user_id, provider, ip=None):
    with transaction() as tx:
        row = tx.execute(select(ProviderCredential).where(
            ProviderCredential.user_id == user_id, ProviderCredential.provider == provider,
        ).with_for_update()).scalar_one_or_none()
        if row is not None:
            tx.delete(row)
            audit("key_removed", user_id, ip, tx=tx, provider=provider)


def rewrap_keys() -> int:
    _, active = crypto.keyring()
    count = 0
    with transaction() as tx:
        for row in tx.execute(select(ProviderCredential).where(ProviderCredential.kek_version != active).with_for_update()).scalars():
            for name, value in asdict(crypto.rewrap(envelope(row), aad(row))).items():
                setattr(row, name, value)
            row.updated_at = _now()
            count += 1
        audit("credential_keys_rewrapped", tx=tx, count=count)
    return count


def preferences(user_id) -> dict:
    with transaction() as tx:
        user = tx.get(User, user_id)
        return dict(user.preferences or {}) if user else {}


def model_catalog(user_id) -> dict:
    rows = {row.provider: row for row in list_credentials(user_id)}
    providers, statuses, defaults = {}, {}, {}
    dev = crypto.dev_server_keys_enabled()
    for provider, adapter in ADAPTERS.items():
        row = rows.get(provider)
        # Dev keys remain opt-in and never mask an explicitly invalid user key.
        dev_available = row is None and dev and bool(os.getenv(adapter.env_key))
        status = row.status if row else "valid" if dev_available else "missing"
        enabled = status == "valid"
        models = row.available_models if row and status != "unchecked" else None
        curated = MODEL_CATALOG[provider]
        labels = {model["id"]: model["label"] for model in curated}
        if models is None:
            choices = list(curated)
        else:
            # Keep curated order, with newly available models after it.
            ids = [m["id"] for m in curated if m["id"] in models]
            ids += sorted(set(models) - set(ids))
            choices = [{"id": model, "label": labels.get(model, model)} for model in ids]
        providers[provider] = choices if enabled else []
        default = DEFAULT_MODEL_BY_PROVIDER[provider]
        defaults[provider] = default if any(m["id"] == default for m in choices) else (choices[0]["id"] if choices else "")
        statuses[provider] = {"status": status, "enabled": enabled, "label": adapter.label,
                              "hint": "" if enabled else "Adicione ou atualize sua chave em Configurações → Chaves.",
                              "settings_url": "/keys"}
    prefs = preferences(user_id)
    selected = prefs.get("default_provider") or DEFAULT_PROVIDER
    if not providers.get(selected):
        selected = next((p for p, models in providers.items() if models), DEFAULT_PROVIDER)
    model = prefs.get("default_model")
    if model not in {m["id"] for m in providers.get(selected, [])}:
        model = defaults[selected]
    return {"providers": providers, "credential_status": statuses, "defaults": defaults,
            "default_provider": selected, "default_model": model}


def save_preferences(user_id, values: dict) -> dict:
    catalog = model_catalog(user_id)
    for prefix in ("default", "llm_fallback"):
        provider = values.get(f"{prefix}_provider")
        model_field = "default_model" if prefix == "default" else "llm_fallback_model_name"
        if not provider:
            values[model_field] = None
            continue
        get_adapter(provider)
        if not catalog["credential_status"][provider]["enabled"]:
            raise MissingCredentialError(provider)
        model = current_model_name(provider, values.get(model_field) or catalog["defaults"][provider])
        if model not in {m["id"] for m in catalog["providers"][provider]}:
            raise ValueError("Modelo indisponível para esta chave.")
        values[model_field] = model
    with transaction() as tx:
        user = tx.get(User, user_id, with_for_update=True)
        user.preferences = {**(user.preferences or {}), **values}
        user.updated_at = _now()
        from app.services.auth_service import audit
        audit("model_preferences_changed", user_id, tx=tx, fields=sorted(values))
        return user.preferences
