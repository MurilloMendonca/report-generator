"""The caller's provider keys and model preferences; keys are never returned."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, SecretStr

from app.pages import render_page
from app.security import client_key, current_user, require_verified_user
from app.services import credential_service as service
from app.services.crypto import EncryptionConfigError
from app.services.llm.errors import MissingCredentialError
from app.services.llm.providers.registry import get_adapter

router = APIRouter()


class KeyBody(BaseModel):
    api_key: SecretStr


class PreferencesBody(BaseModel):
    default_provider: str | None = None
    default_model: str | None = None
    llm_fallback_provider: str | None = None
    llm_fallback_model_name: str | None = None


def _provider(provider):
    try:
        get_adapter(provider)
    except ValueError:
        raise HTTPException(404, detail="Provedor desconhecido.") from None


def _run(action):
    try:
        return action()
    except service.CredentialValidationError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": str(exc)}) from None
    except MissingCredentialError as exc:
        raise HTTPException(400, detail=exc.as_error_dict()) from None
    except EncryptionConfigError:
        raise HTTPException(503, detail={"code": "credential_storage_unavailable", "message": "O armazenamento de chaves está indisponível. Contate o administrador."}) from None
    except ValueError:
        raise HTTPException(400, detail={"code": "invalid_model_preference", "message": "Escolha um provedor e modelo disponíveis para suas chaves."}) from None


@router.get("/api/credentials")
def list_credentials(request: Request):
    return {"credentials": [service.payload(row) for row in service.list_credentials(current_user(request).id)]}


@router.put("/api/credentials/{provider}")
def put_credential(provider: str, request: Request, body: KeyBody):
    user = require_verified_user(request)
    _provider(provider)
    return _run(lambda: service.put_credential(user.id, provider, body.api_key.get_secret_value(), client_key(request)))


@router.post("/api/credentials/{provider}/test")
def test_credential(provider: str, request: Request):
    user = require_verified_user(request)
    _provider(provider)
    return _run(lambda: service.test_credential(user.id, provider, client_key(request)))


@router.delete("/api/credentials/{provider}")
def delete_credential(provider: str, request: Request):
    _provider(provider)
    service.delete_credential(current_user(request).id, provider, client_key(request))
    return {"status": "OK"}


@router.get("/api/model-preferences")
def model_preferences(request: Request):
    prefs = service.preferences(current_user(request).id)
    return {key: prefs.get(key) for key in PreferencesBody.model_fields}


@router.put("/api/model-preferences")
def save_preferences(request: Request, body: PreferencesBody):
    return _run(lambda: service.save_preferences(current_user(request).id, body.model_dump()))


@router.get("/keys", response_class=HTMLResponse, include_in_schema=False)
def keys_page():
    return render_page("keys.html")
