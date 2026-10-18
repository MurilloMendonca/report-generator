"""Resolve credentials only for the current operation's report owner."""
import os

from app.services import credential_service, crypto
from app.services.llm.errors import InvalidCredentialError, MissingCredentialError
from app.services.llm.providers.registry import get_adapter
from app.services.session_manager import get_report_owner


def operation_user_id(context: dict, provider: str):
    # Use the authoritative report owner, never an owner_id inside report JSON.
    job_id = context.get("job_id")
    if job_id and context.get("_owner_job_id") != job_id:
        owner = get_report_owner(job_id)
        if owner is None:
            raise MissingCredentialError(provider)
        context["user_id"] = str(owner)
        context["_owner_job_id"] = job_id
    user_id = context.get("user_id")
    if not user_id:
        raise MissingCredentialError(provider)
    return user_id


def resolve_api_key(user_id, provider: str) -> str:
    from app.services.llm.client import current_llm_context
    context = current_llm_context()
    dev = crypto.dev_server_keys_enabled()
    if context.get("job_id") or context.get("user_id"):
        user_id = operation_user_id(context, provider)
    if not user_id:
        raise MissingCredentialError(provider)
    row = credential_service.get_credential(user_id, provider)
    cache = context.setdefault("_credentials", {})
    cache_key = (str(user_id), provider)
    if row is None:
        cache.pop(cache_key, None)
        if dev:
            key = os.getenv(get_adapter(provider).env_key)
            if key:
                return key
        raise MissingCredentialError(provider)
    if row.status == "invalid":
        cache.pop(cache_key, None)
        raise InvalidCredentialError(provider)
    cached = cache.get(cache_key)
    # Recheck status and identity on each call, decrypt only once per operation.
    if cached is None or cached["id"] != row.id or cached["ciphertext"] != row.ciphertext:
        cached = {"id": row.id, "ciphertext": row.ciphertext, "key": credential_service.decrypt(row)}
        cache[cache_key] = cached
    return cached["key"]


def invalidate_current_credential(provider):
    from app.services.llm.client import current_llm_context
    context = current_llm_context()
    user_id = operation_user_id(context, provider)
    cached = context.get("_credentials", {}).get((str(user_id), provider))
    if cached:
        credential_service.mark_invalid(user_id, provider, cached["id"], cached["ciphertext"])


def operation_preferences(context):
    if "_preferences" not in context:
        user_id = operation_user_id(context, context.get("provider") or "deepseek")
        context["_preferences"] = credential_service.preferences(user_id)
    return context["_preferences"]
