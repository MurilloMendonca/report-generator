"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-18 14:00:18.947340
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('revoked_tokens',
    sa.Column('nonce', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('nonce')
    )
    op.create_index(op.f('ix_revoked_tokens_expires_at'), 'revoked_tokens', ['expires_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('display_name', sa.Text(), server_default='', nullable=False),
    sa.Column('role', sa.Text(), server_default='user', nullable=False),
    sa.Column('status', sa.Text(), server_default='active', nullable=False),
    sa.Column('is_demo', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('preferences', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('layout_templates',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('reports',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.Text(), server_default='', nullable=False),
    sa.Column('current_step', sa.Text(), server_default='', nullable=False),
    sa.Column('pdf_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('storage_bytes', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_reports_owner_updated', 'reports', ['owner_id', sa.literal_column('updated_at DESC')], unique=False)
    op.create_index('ix_reports_updated', 'reports', [sa.literal_column('updated_at DESC')], unique=False)


def downgrade():
    op.drop_index('ix_reports_updated', table_name='reports')
    op.drop_index('ix_reports_owner_updated', table_name='reports')
    op.drop_table('reports')
    op.drop_table('layout_templates')
    op.drop_table('users')
    op.drop_index(op.f('ix_revoked_tokens_expires_at'), table_name='revoked_tokens')
    op.drop_table('revoked_tokens')
