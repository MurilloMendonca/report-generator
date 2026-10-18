"""Alembic environment. app/db.py passes an open connection; the CLI uses DATABASE_URL."""
from alembic import context
from sqlalchemy import create_engine

from app.models.db import Base

target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    from app.db import database_url

    engine = create_engine(database_url())
    with engine.begin() as conn:
        run(conn)
    engine.dispose()
