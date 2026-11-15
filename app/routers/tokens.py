"""Personal API tokens of the signed-in user. Browser session only: tokens cannot manage tokens."""
from fastapi import APIRouter, Request

from app.models.api_tokens import CreateApiToken
from app.security import client_key, require_verified_user
from app.services import api_token_service

router = APIRouter(tags=["account"])


@router.get("/api/tokens")
def list_api_tokens(request: Request):
    return {"tokens": api_token_service.list_tokens(require_verified_user(request).id)}


@router.post("/api/tokens", status_code=201)
def create_api_token(request: Request, payload: CreateApiToken):
    """Create a token. The response is the only time the token itself is shown."""
    user = require_verified_user(request)
    return api_token_service.create_token(user.id, payload.name, payload.expires_in_days, client_key(request))


@router.delete("/api/tokens/{token_id}")
def revoke_api_token(request: Request, token_id: str):
    user = require_verified_user(request)
    return api_token_service.revoke_token(user.id, token_id, client_key(request))
