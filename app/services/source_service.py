"""Account-owned connections and datasets. Only encrypted secrets reach storage."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import threading
import uuid

from cryptography.exceptions import InvalidTag
from sqlalchemy import func, select

from app.db import transaction
from app.models.db import SourceConnection, SourceDataset, User
from app.models.sources import ApiDataset, ConnectionBody, DatasetBody, PostgresDataset, SourceSelection
from app.services import crypto, source_connectors
from app.services.auth_service import audit
from app.services.rate_limit import LIMITS, limiter
from app.services.source_errors import SourceError

LIMITS.setdefault("source_read", (20, 60))
_slots = threading.BoundedSemaphore(4)
_owners = {}
_owners_lock = threading.Lock()


def aad(row):
    return f"source|{row.owner_id}|{row.id}".encode()


def envelope(row):
    return crypto.EncryptedSecret(**{name: getattr(row, name) for name in crypto.EncryptedSecret.__dataclass_fields__})


def decrypt(row):
    try:
        return crypto.decrypt_secret(envelope(row), aad(row))
    except (InvalidTag, ValueError, crypto.EncryptionConfigError):
        raise SourceError("source_credentials", "A credencial desta conexão não está disponível. Salve-a novamente.", 503) from None


def _id(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise SourceError("source_not_found", "Fonte não encontrada.", 404) from None


def _connection(tx, owner_id, connection_id, *, lock=False):
    query = select(SourceConnection).where(SourceConnection.id == _id(connection_id), SourceConnection.owner_id == owner_id)
    row = tx.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if row is None:
        raise SourceError("source_not_found", "Conexão não encontrada.", 404)
    return row


def _dataset(tx, owner_id, dataset_id, *, lock=False):
    query = select(SourceDataset).where(SourceDataset.id == _id(dataset_id), SourceDataset.owner_id == owner_id)
    row = tx.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if row is None:
        raise SourceError("source_not_found", "Conjunto de dados não encontrado.", 404)
    return row


def connection_payload(row):
    return {"id": str(row.id), "name": row.name, "kind": row.kind, "config": row.config,
            "version": row.version, "has_secret": row.kind == "postgres" or row.config.get("auth_mode") != "none"}


def dataset_payload(row):
    return {"id": str(row.id), "connection_id": str(row.connection_id), "name": row.name,
            "definition": row.definition, "version": row.version,
            "filename": f"dataset_{row.id.hex}.json"}


def catalog(owner_id):
    with transaction() as tx:
        connections = list(tx.execute(select(SourceConnection).where(SourceConnection.owner_id == owner_id).order_by(SourceConnection.created_at)).scalars())
        datasets = list(tx.execute(select(SourceDataset).where(SourceDataset.owner_id == owner_id).order_by(SourceDataset.created_at)).scalars())
        return {"connections": [connection_payload(row) for row in connections], "datasets": [dataset_payload(row) for row in datasets]}


def save_connection(owner_id, body: ConnectionBody, connection_id=None, ip=None):
    crypto.keyring()
    with transaction() as tx:
        if tx.get(User, owner_id, with_for_update=True) is None:
            raise SourceError("source_not_found", "Conta não encontrada.", 404)
        row = _connection(tx, owner_id, connection_id, lock=True) if connection_id else None
        if row and row.kind != body.kind:
            raise SourceError("source_kind", "Crie outra conexão para usar um tipo de fonte diferente.")
        if row is None:
            count = tx.scalar(select(func.count()).select_from(SourceConnection).where(SourceConnection.owner_id == owner_id))
            if count >= 20:
                raise SourceError("source_limit", "Você já tem 20 conexões salvas.")
            row = SourceConnection(id=uuid.uuid4(), owner_id=owner_id, kind=body.kind, version=0)
            tx.add(row)
        needs_secret = body.kind == "postgres" or body.config.get("auth_mode") != "none"
        if body.secret is None and needs_secret and (row.version == 0 or row.config != body.config):
            raise SourceError("source_credentials", "Informe a credencial ao criar ou alterar o destino/autenticação da conexão.")
        if body.secret is not None or not needs_secret:
            secret = body.secret.get_secret_value() if body.secret is not None and needs_secret else ""
            if needs_secret and not secret:
                raise SourceError("source_credentials", "Informe a credencial desta conexão.")
            for key, value in asdict(crypto.encrypt_secret(secret, aad(row))).items():
                setattr(row, key, value)
        row.name, row.config, row.version = body.name.strip(), body.config, row.version + 1
        row.updated_at = datetime.now(timezone.utc)
        audit("source_connection_saved", owner_id, ip, tx=tx, source_id=str(row.id), kind=row.kind)
        return connection_payload(row)


def delete_connection(owner_id, connection_id, ip=None):
    with transaction() as tx:
        row = _connection(tx, owner_id, connection_id, lock=True)
        tx.delete(row)
        audit("source_connection_deleted", owner_id, ip, tx=tx, source_id=str(row.id))


def save_dataset(owner_id, body: DatasetBody, dataset_id=None, ip=None):
    with transaction() as tx:
        if tx.get(User, owner_id, with_for_update=True) is None:
            raise SourceError("source_not_found", "Conta não encontrada.", 404)
        connection = _connection(tx, owner_id, body.connection_id)
        definition = (ApiDataset if connection.kind == "api" else PostgresDataset).model_validate(body.definition).model_dump()
        if len(json.dumps(definition)) > 65536:
            raise SourceError("source_limit", "A definição do conjunto de dados é muito grande.")
        row = _dataset(tx, owner_id, dataset_id, lock=True) if dataset_id else None
        if row is None:
            count = tx.scalar(select(func.count()).select_from(SourceDataset).where(SourceDataset.owner_id == owner_id))
            if count >= 100:
                raise SourceError("source_limit", "Você já tem 100 conjuntos de dados salvos.")
            row = SourceDataset(id=uuid.uuid4(), owner_id=owner_id, version=0)
            tx.add(row)
        row.name, row.connection_id, row.definition = body.name.strip(), connection.id, definition
        row.version, row.updated_at = row.version + 1, datetime.now(timezone.utc)
        audit("source_dataset_saved", owner_id, ip, tx=tx, dataset_id=str(row.id), source_id=str(connection.id))
        return dataset_payload(row)


def delete_dataset(owner_id, dataset_id, ip=None):
    with transaction() as tx:
        row = _dataset(tx, owner_id, dataset_id, lock=True)
        tx.delete(row)
        audit("source_dataset_deleted", owner_id, ip, tx=tx, dataset_id=str(row.id))


def select_datasets(owner_id, selections):
    references, seen = [], set()
    with transaction() as tx:
        for raw in selections:
            selection = raw if isinstance(raw, SourceSelection) else SourceSelection.model_validate(raw)
            if len(json.dumps(selection.parameters)) > 65536:
                raise SourceError("source_limit", "Os parâmetros de um conjunto não podem exceder 64 KB.")
            dataset = _dataset(tx, owner_id, selection.dataset_id)
            connection = _connection(tx, owner_id, dataset.connection_id)
            if dataset.id in seen or set(selection.parameters) - set(dataset.definition["parameters"]):
                raise SourceError("source_parameters", "Escolha conjuntos distintos e use apenas seus parâmetros definidos.")
            seen.add(dataset.id)
            references.append({"dataset_id": str(dataset.id), "dataset_version": dataset.version,
                               "connection_id": str(connection.id), "connection_version": connection.version,
                               "parameters": selection.parameters})
    return references


@contextmanager
def source_slot(owner_id):
    if limiter.check_and_hit("source_read", str(owner_id)):
        raise SourceError("source_rate_limit", "Aguarde antes de testar ou visualizar outra fonte.", 429)
    with _owners_lock:
        slot = _owners.setdefault(owner_id, threading.BoundedSemaphore(1))
    if not slot.acquire(blocking=False):
        raise SourceError("source_busy", "Você já tem uma leitura de fonte em andamento.", 429)
    try:
        if not _slots.acquire(blocking=False):
            raise SourceError("source_busy", "Há outras leituras em andamento. Tente novamente.", 429)
        try:
            yield
        finally:
            _slots.release()
    finally:
        slot.release()


def test_connection(owner_id, connection_id):
    with transaction() as tx:
        row = _connection(tx, owner_id, connection_id)
    with source_slot(owner_id):
        return source_connectors.test_connection(row.kind, row.config, decrypt(row))


def read_dataset(owner_id, reference, *, preview=False):
    with transaction() as tx:
        dataset = _dataset(tx, owner_id, reference["dataset_id"])
        connection = _connection(tx, owner_id, dataset.connection_id)
        if reference.get("dataset_version", dataset.version) != dataset.version or reference.get("connection_version", connection.version) != connection.version:
            raise SourceError("source_changed", "A fonte mudou após a seleção. Selecione-a novamente antes de importar.", 409)
    body, details = source_connectors.read_dataset(connection.kind, connection.config, decrypt(connection), dataset.definition,
                                                   reference.get("parameters") or {}, preview=preview)
    return body, {**details, "type": "saved_dataset", "dataset_id": str(dataset.id), "dataset_name": dataset.name,
                  "dataset_version": dataset.version, "connection_id": str(connection.id), "connection_version": connection.version,
                  "kind": connection.kind, "parameters": {**dataset.definition["parameters"], **(reference.get("parameters") or {})},
                  "fetched_at": datetime.now(timezone.utc).isoformat()}


def preview_dataset(owner_id, selection):
    reference = select_datasets(owner_id, [selection])[0]
    with source_slot(owner_id):
        body, details = read_dataset(owner_id, reference, preview=True)
    if len(body) > 65536:
        raise SourceError("source_limit", "A prévia excedeu 64 KB. Restrinja os parâmetros para visualizá-la.")
    return {"rows": json.loads(body), "row_count": details["row_count"], "preview": True, "complete": False}


def rewrap_source_keys(tx, active):
    count = 0
    for row in tx.execute(select(SourceConnection).where(SourceConnection.kek_version != active).with_for_update()).scalars():
        for name, value in asdict(crypto.rewrap(envelope(row), aad(row))).items():
            setattr(row, name, value)
        count += 1
    return count
