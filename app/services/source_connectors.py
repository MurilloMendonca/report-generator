"""Bounded connector reads. Credentials are used here, never in generated code."""
import base64
import asyncio
from contextlib import asynccontextmanager
import ipaddress
import json
import os
import time
import uuid
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from fastapi import HTTPException

from app import db
from app.models.sources import PostgresConnectionConfig
from app.services import api_sources, source_network
from app.services.source_errors import SourceError
from app.utils.tabular import json_records, parse_json

MAX_ROWS = int(os.getenv("PDF_REPORT_SOURCE_MAX_ROWS", "100000"))
MAX_BYTES = int(os.getenv("PDF_REPORT_SOURCE_MAX_BYTES", str(10 * 1024 * 1024)))
TIMEOUT_SECONDS = int(os.getenv("PDF_REPORT_SOURCE_TIMEOUT_SECONDS", "60"))


def auth_headers(config, secret):
    mode = config["auth_mode"]
    if mode == "none":
        return {}
    if not secret or any(ord(char) < 32 or ord(char) == 127 for char in secret):
        raise SourceError("source_credentials", "Atualize a credencial desta conexão.")
    if mode == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if mode == "api_key":
        return {config["header_name"]: secret}
    credential = base64.b64encode(f"{config['username']}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {credential}"}


def _origin(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise SourceError("source_destination", "O destino deve usar HTTPS e a mesma origem da conexão.")
    return parsed.scheme, (parsed.hostname or "").encode("idna").decode("ascii").lower(), parsed.port or 443


def api_dataset_url(config, definition, parameters):
    url = urljoin(config["base_url"].rstrip("/") + "/", definition["endpoint"])
    if _origin(url) != _origin(config["base_url"]):
        raise SourceError("source_destination", "O endpoint deve pertencer à origem da conexão.")
    parsed = urlsplit(url)
    query_parameters = {key: str(value).lower() if isinstance(value, bool) else value
                        for key, value in parameters.items() if value is not None}
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_parameters), ""))


def _api_fetch(url, headers, deadline, remaining_bytes):
    try:
        if deadline <= time.monotonic() or remaining_bytes <= 0:
            raise SourceError("source_limit", "A importação excedeu o limite de tempo ou tamanho.")
        body, _hostname = api_sources.fetch_json_snapshot(
            url, auth_headers=headers, address_validator=source_network.address_allowed,
            timeout_seconds=deadline - time.monotonic(), max_bytes=remaining_bytes,
        )
        return body, parse_json(body.decode("utf-8-sig"))
    except HTTPException as exc:
        # Never forward upstream bodies, URLs or headers through connector errors.
        import re
        status = re.search(r"API returned HTTP (\d{3})", str(exc.detail))
        if status:
            raise SourceError("source_http_error", f"A API respondeu HTTP {status[1]}. Verifique o endpoint e a autenticação.") from None
        raise SourceError("source_api_error", "Não foi possível importar JSON da API. Verifique destino, formato e limites.") from None


def fetch_api(config, secret, definition, parameters, *, preview=False):
    headers = auth_headers(config, secret)
    deadline = time.monotonic() + (20 if preview else TIMEOUT_SECONDS)
    url = api_dataset_url(config, definition, parameters)
    records, visited, total_bytes = [], set(), 0
    page = definition["first_page"]
    for _ in range(definition["max_pages"]):
        if definition["pagination"] == "page":
            url = api_dataset_url(config, definition, {**parameters, definition["page_parameter"]: page})
        if url in visited:
            raise SourceError("source_incomplete", "A paginação repetiu uma página. Nenhum snapshot parcial foi aceito.")
        visited.add(url)
        raw, payload = _api_fetch(url, headers, deadline, MAX_BYTES - total_bytes)
        total_bytes += len(raw)
        try:
            selected = api_sources.select_records(payload, definition["records_path"])
            current = [] if selected == [] else json_records(selected)[0]
        except (ValueError, HTTPException):
            raise SourceError("source_records", "Selecione um caminho de registros válido para esta API.") from None
        records.extend(current)
        if preview:
            return records[:5], {"complete": False, "preview": True, "pages": len(visited)}
        if len(records) > MAX_ROWS:
            raise SourceError("source_limit", "A fonte excedeu o limite de linhas. Restrinja os parâmetros e tente novamente.")
        if definition["pagination"] == "none":
            break
        if definition["pagination"] == "page":
            if not current:
                break
            page += 1
            continue
        try:
            next_url = api_sources.select_records(payload, definition["next_path"])
        except HTTPException:
            raise SourceError("source_incomplete", "O caminho de próxima página não foi encontrado.") from None
        if next_url is None or next_url == "":
            break
        if not isinstance(next_url, str):
            raise SourceError("source_incomplete", "A próxima página precisa ser uma URL da mesma origem.")
        next_url = urljoin(url, next_url)
        if _origin(next_url) != _origin(config["base_url"]):
            raise SourceError("source_destination", "A paginação tentou mudar a origem da conexão.")
        url = next_url
    else:
        raise SourceError("source_incomplete", "A paginação atingiu o limite de páginas. Nenhum snapshot parcial foi aceito.")
    return records, {"complete": True, "pages": len(visited)}


def _reject_application_database(config, address):
    from sqlalchemy.engine import make_url
    try:
        application = make_url(db.database_url())
    except db.DatabaseNotConfigured:
        return
    if config["database"] != application.database or config["port"] != (application.port or 5432):
        return
    if config["host"].casefold() == str(application.host).casefold():
        raise SourceError("source_destination", "O banco interno da aplicação não pode ser usado como fonte.")
    try:
        _host, app_address = source_network.resolve_host(application.host, application.port or 5432)
        if ipaddress.ip_address(address) == ipaddress.ip_address(app_address):
            raise SourceError("source_destination", "O banco interno da aplicação não pode ser usado como fonte.")
    except ValueError:
        pass


@asynccontextmanager
async def postgres_connection(config, secret):
    try:
        config = PostgresConnectionConfig.model_validate(config).model_dump()
        host, address = source_network.resolve_host(config["host"], config["port"])
        _reject_application_database(config, address)
        # TLS is verified unless this connection explicitly opts out. Never
        # retry a TLS failure without encryption. Pin the approved address in
        # either mode, retaining host for TLS hostname verification.
        transport = {"sslmode": config["sslmode"]}
        if config["sslmode"] == "verify-full":
            transport["sslrootcert"] = "system"
        async with await psycopg.AsyncConnection.connect(
            host=host, hostaddr=address, port=config["port"], dbname=config["database"],
            user=config["username"], password=secret, passfile="/dev/null",
            **transport, gssencmode="disable", connect_timeout=5,
            application_name="pdf-report-source",
            options="-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=20000 -c search_path=pg_catalog",
        ) as connection:
            await connection.execute("SET TRANSACTION READ ONLY")
            privileged = await (await connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
                "WHERE (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
                "OR rolname IN ('pg_read_server_files', 'pg_write_server_files', 'pg_execute_server_program')) "
                "AND pg_catalog.pg_has_role(CURRENT_USER, oid, 'MEMBER'))"
            )).fetchone()
            if not privileged or privileged[0]:
                raise SourceError("source_database_role", "Use uma conta de banco restrita para leitura, sem privilégios administrativos.")
            yield connection
    except SourceError:
        raise
    except psycopg.OperationalError as exc:
        if "server does not support SSL" in str(exc):
            raise SourceError(
                "source_database_tls",
                "O servidor não oferece TLS. Para uma fonte de dados públicos como RNAcentral, "
                "selecione Sem TLS na conexão e informe novamente a senha.",
            ) from None
        raise SourceError("source_database_error", "Não foi possível ler o PostgreSQL. Verifique rede aprovada, TLS, credenciais e a consulta somente de leitura.") from None
    except Exception:
        raise SourceError("source_database_error", "Não foi possível ler o PostgreSQL. Verifique rede aprovada, TLS, credenciais e a consulta somente de leitura.") from None


async def _fetch_postgres(config, secret, definition, parameters, *, preview=False):
    row_limit = 5 if preview else MAX_ROWS
    async with postgres_connection(config, secret) as connection:
        if definition["mode"] == "table":
            query = sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(definition["schema_name"]), sql.Identifier(definition["table_name"]))
        else:
            query = sql.SQL(definition["query"].rstrip().removesuffix(";"))
        # Validate one statement using the extended protocol before opening a
        # streaming cursor. User SQL remains inside a derived SELECT.
        template = sql.SQL(
            "SELECT CASE WHEN pg_catalog.octet_length(payload) > {bytes} THEN NULL ELSE payload END "
            "FROM (SELECT pg_catalog.row_to_json(report_source)::text AS payload "
            "FROM ({query}) AS report_source LIMIT {limit}) AS bounded_source"
        )
        def bounded(limit):
            return template.format(query=query, bytes=sql.Literal(MAX_BYTES), limit=sql.Literal(limit))
        await connection.execute(bounded(0), parameters, prepare=True)
        records, byte_count = [], 2
        async with connection.cursor(name=f"source_{uuid.uuid4().hex}") as cursor:
            cursor.itersize = 1
            await cursor.execute(bounded(row_limit + 1), parameters)
            async for row in cursor:
                if row[0] is None:
                    raise SourceError("source_limit", "Uma linha excedeu o limite de tamanho da fonte.")
                byte_count += len(row[0].encode("utf-8")) + 1
                if byte_count > MAX_BYTES:
                    raise SourceError("source_limit", "A fonte excedeu o limite de tamanho. Restrinja a consulta.")
                records.append(parse_json(row[0]))
                if len(records) > row_limit:
                    if preview:
                        break
                    raise SourceError("source_limit", "A fonte excedeu o limite de linhas. Restrinja a consulta.")
        return records[:row_limit], {"complete": not preview, "preview": preview}


def _bounded_database_read(coroutine, timeout):
    async def run():
        return await asyncio.wait_for(coroutine, timeout=timeout)
    try:
        return asyncio.run(run())
    except TimeoutError:
        raise SourceError("source_timeout", "A leitura do banco excedeu o tempo permitido. Restrinja a consulta.") from None


def fetch_postgres(config, secret, definition, parameters, *, preview=False):
    return _bounded_database_read(
        _fetch_postgres(config, secret, definition, parameters, preview=preview),
        20 if preview else TIMEOUT_SECONDS,
    )


async def _test_postgres(config, secret):
    async with postgres_connection(config, secret) as connection:
        await connection.execute("SELECT 1")


def test_connection(kind, config, secret):
    try:
        if kind == "api":
            _api_fetch(config["base_url"], auth_headers(config, secret), time.monotonic() + 20, MAX_BYTES)
        else:
            _bounded_database_read(_test_postgres(config, secret), 20)
    except SourceError:
        raise
    except Exception:
        raise SourceError("source_connection_error", "Não foi possível testar a conexão. Verifique destino, autenticação e formato da resposta.") from None
    return {"ok": True, "message": "Conexão testada com sucesso."}


def read_dataset(kind, config, secret, definition, parameters, *, preview=False):
    if set(parameters) - set(definition["parameters"]):
        raise SourceError("source_parameters", "Use apenas os parâmetros definidos neste conjunto de dados.")
    merged = {**definition["parameters"], **parameters}
    try:
        fetch = fetch_api if kind == "api" else fetch_postgres
        records, details = fetch(config, secret, definition, merged, preview=preview)
        if not records:
            raise SourceError("source_empty", "A fonte não retornou registros para os parâmetros selecionados.")
        body = json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(body) > MAX_BYTES:
            raise SourceError("source_limit", "A fonte excedeu o limite de tamanho.")
        # Endpoints that echo authentication material must never feed the LLM.
        forbidden = [secret] if secret else []
        if kind == "api":
            for value in auth_headers(config, secret).values():
                forbidden.append(value)
                if value.startswith("Basic "):
                    forbidden.append(value.removeprefix("Basic "))
        def contains_secret(value):
            if isinstance(value, str):
                return any(value == item or (len(item) >= 8 and item in value) for item in forbidden)
            if isinstance(value, dict):
                return any(contains_secret(key) or contains_secret(item) for key, item in value.items())
            if isinstance(value, list):
                return any(contains_secret(item) for item in value)
            return False
        if contains_secret(records):
            raise SourceError("source_secret_in_data", "A fonte retornou material de autenticação. Escolha um endpoint de dados.")
        return body, {**details, "row_count": len(records)}
    except SourceError:
        raise
    except Exception:
        raise SourceError("source_read_error", "Não foi possível importar o conjunto de dados. Verifique seu formato e configuração.") from None
