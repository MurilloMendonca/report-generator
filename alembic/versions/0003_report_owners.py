"""every report has an owner; user templates are private

Reports and non-built-in templates without an owner (made before accounts
existed) go to the demo account, or the first admin, or the oldest account.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

# The built-in template ids at the time of this migration.
BUILT_IN_TEMPLATES = ("executive_standard", "data_heavy", "document_text_only")


def upgrade():
    conn = op.get_bind()
    orphans = conn.execute(sa.text("SELECT count(*) FROM reports WHERE owner_id IS NULL")).scalar()
    owner = conn.execute(sa.text(
        "SELECT id FROM users ORDER BY is_demo DESC, (role = 'admin') DESC, created_at LIMIT 1"
    )).scalar()
    if orphans and owner is None:
        raise RuntimeError(
            f"{orphans} report(s) have no owner and there is no account to give them to. "
            "Create one first: PDF_REPORT_AUTO_MIGRATE=0 python -m app.cli create-admin --email ..."
        )
    if owner is not None:
        conn.execute(sa.text("UPDATE reports SET owner_id = :owner WHERE owner_id IS NULL"), {"owner": owner})
        conn.execute(
            sa.text("UPDATE layout_templates SET owner_id = :owner WHERE owner_id IS NULL AND NOT (id = ANY(:builtins))"),
            {"owner": owner, "builtins": list(BUILT_IN_TEMPLATES)},
        )
    op.alter_column('reports', 'owner_id', existing_type=sa.UUID(), nullable=False)


def downgrade():
    op.alter_column('reports', 'owner_id', existing_type=sa.UUID(), nullable=True)
