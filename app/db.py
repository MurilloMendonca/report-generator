"""Postgres engine, transactions and schema migrations.

`DATABASE_URL` points at the app's database. Under `APP_ENV=test` the app uses
`TEST_DATABASE_URL` instead (default: the `repgen_test` database of the local
Postgres in `compose.dev.yml`), creates it if needed, and empties it once per
test run.

The schema is brought up to date with Alembic the first time the engine is
used (`PDF_REPORT_AUTO_MIGRATE=0` turns that off). The app runs as a single
process, and an advisory lock keeps a concurrent CLI run from migrating at the
same time.
"""
import json
import logging
import math
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://repgen:repgen@127.0.0.1:5433/repgen_test"
MIGRATION_LOCK_ID = 7_310_241  # any constant shared by every process that migrates

_engine: Engine | None = None
_sessionmaker: sessionmaker | None = None
_engine_lock = threading.Lock()


class DatabaseNotConfigured(RuntimeError):
    pass


def is_test_env() -> bool:
    return os.getenv("APP_ENV", "").lower() == "test"


def database_url() -> str:
    if is_test_env():
        return os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. For local development start Postgres with "
            "`docker compose -f compose.dev.yml up -d` and set "
            "DATABASE_URL=postgresql+psycopg://repgen:repgen@127.0.0.1:5433/repgen"
        )
    return url


def _clean_for_json(value):
    """Postgres JSONB rejects NUL characters and NaN/Infinity, which pandas output can contain."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key).replace("\x00", ""): _clean_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_for_json(item) for item in value]
    return value


def json_dumps(value) -> str:
    return json.dumps(_clean_for_json(value), ensure_ascii=False, default=str)


def _create_test_database(url) -> None:
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(f"Refusing to use {url.database!r} for tests: the name must end with '_test'.")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()


def run_migrations(engine: Engine) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        config.attributes["connection"] = conn
        command.upgrade(config, "head")


def _empty_test_database(engine: Engine) -> None:
    from app.models.db import Base

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


def _build_engine() -> Engine:
    url = make_url(database_url())
    if is_test_env():
        _create_test_database(url)
    engine = create_engine(
        url,
        json_serializer=json_dumps,
        pool_pre_ping=True,
        pool_size=int(os.getenv("PDF_REPORT_DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("PDF_REPORT_DB_MAX_OVERFLOW", "10")),
    )
    try:
        if os.getenv("PDF_REPORT_AUTO_MIGRATE", "1") == "1":
            run_migrations(engine)
        if is_test_env():
            _empty_test_database(engine)
    except Exception as exc:
        engine.dispose()
        if is_test_env():
            raise RuntimeError(
                f"Test database at {url.render_as_string(hide_password=True)} is not reachable ({exc.__class__.__name__}). "
                "Start it with `docker compose -f compose.dev.yml up -d`, or set TEST_DATABASE_URL."
            ) from exc
        raise
    return engine


def get_engine() -> Engine:
    global _engine, _sessionmaker
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                engine = _build_engine()
                _sessionmaker = sessionmaker(engine, expire_on_commit=False)
                _engine = engine
    return _engine


@contextmanager
def transaction():
    """One database transaction: committed on success, rolled back on error."""
    get_engine()
    with _sessionmaker.begin() as session:
        yield session


def new_session() -> Session:
    get_engine()
    return _sessionmaker()


def dispose_engine() -> None:
    global _engine, _sessionmaker
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _sessionmaker = None
