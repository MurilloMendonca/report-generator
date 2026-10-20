"""The caller's saved input connections and datasets."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.models.sources import ConnectionBody, DatasetBody, SourceSelection
from app.pages import render_page
from app.security import client_key, current_user, require_verified_user
from app.services import source_service
from app.services.crypto import EncryptionConfigError
from app.services.source_errors import SourceError

router = APIRouter()


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    current_user(request)
    return render_page("sources.html")


def source_action(action):
    try:
        return action()
    except SourceError as exc:
        raise HTTPException(exc.status_code, detail=exc.as_error_dict()) from None
    except EncryptionConfigError:
        raise HTTPException(503, detail={"code": "source_credentials", "message": "O armazenamento de credenciais não está configurado. Contate o administrador."}) from None
    except ValidationError:
        raise HTTPException(422, detail={"code": "source_definition", "message": "Revise os campos da definição do conjunto de dados."}) from None


@router.get("/api/data-sources")
def list_sources(request: Request):
    return source_service.catalog(current_user(request).id)


@router.post("/api/data-sources/connections", status_code=201)
def create_connection(request: Request, body: ConnectionBody):
    user = require_verified_user(request)
    return source_action(lambda: source_service.save_connection(user.id, body, ip=client_key(request)))


@router.put("/api/data-sources/connections/{connection_id}")
def update_connection(request: Request, connection_id: UUID, body: ConnectionBody):
    user = require_verified_user(request)
    return source_action(lambda: source_service.save_connection(user.id, body, connection_id, client_key(request)))


@router.delete("/api/data-sources/connections/{connection_id}")
def delete_connection(request: Request, connection_id: UUID):
    user = current_user(request)
    source_action(lambda: source_service.delete_connection(user.id, connection_id, client_key(request)))
    return {"status": "OK"}


@router.post("/api/data-sources/connections/{connection_id}/test")
def test_connection(request: Request, connection_id: UUID):
    user = require_verified_user(request)
    return source_action(lambda: source_service.test_connection(user.id, connection_id))


@router.post("/api/data-sources/datasets", status_code=201)
def create_dataset(request: Request, body: DatasetBody):
    user = require_verified_user(request)
    return source_action(lambda: source_service.save_dataset(user.id, body, ip=client_key(request)))


@router.put("/api/data-sources/datasets/{dataset_id}")
def update_dataset(request: Request, dataset_id: UUID, body: DatasetBody):
    user = require_verified_user(request)
    return source_action(lambda: source_service.save_dataset(user.id, body, dataset_id, client_key(request)))


@router.delete("/api/data-sources/datasets/{dataset_id}")
def delete_dataset(request: Request, dataset_id: UUID):
    user = current_user(request)
    source_action(lambda: source_service.delete_dataset(user.id, dataset_id, client_key(request)))
    return {"status": "OK"}


@router.post("/api/data-sources/datasets/{dataset_id}/preview")
def preview_dataset(request: Request, dataset_id: UUID, body: SourceSelection):
    user = require_verified_user(request)
    if dataset_id != body.dataset_id:
        raise HTTPException(400, detail="Dataset IDs must match")
    return source_action(lambda: source_service.preview_dataset(user.id, body))
