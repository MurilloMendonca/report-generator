"""Database tables (plan section 4). Phase 2 adds the auth tables."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _created():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stored lowercased; the unique index makes addresses case-insensitive.
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)  # set in Phase 2
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Report(Base):
    """One report. `state` is the session document the pipeline reads and writes;
    the other columns copy the fields that listings and checks query."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # the job_id
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    current_step: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    pdf_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        Index("ix_reports_owner_updated", "owner_id", text("updated_at DESC")),
        Index("ix_reports_updated", text("updated_at DESC")),
    )


class LayoutTemplate(Base):
    """Layout templates. Until Phase 3 every template is shared (owner_id NULL)."""

    __tablename__ = "layout_templates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class RevokedToken(Base):
    """Logged-out session tokens. Replaced by server-side sessions in Phase 2."""

    __tablename__ = "revoked_tokens"

    nonce: Mapped[str] = mapped_column(Text, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
