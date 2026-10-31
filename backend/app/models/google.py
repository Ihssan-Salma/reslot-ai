from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class GoogleConnection(Base):
    __tablename__ = "google_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        UniqueConstraint("tenant_id", "id", name="uq_google_connections_tenant_connection"),
        UniqueConstraint(
            "tenant_id", "google_subject", name="uq_google_connections_tenant_subject"
        ),
        CheckConstraint(
            "connection_status IN ('connected', 'reconnect_required', "
            "'disconnect_pending', 'disconnected')",
            name="google_connection_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    google_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(254))
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_scopes: Mapped[list[str]] = mapped_column(JSONB)
    connection_status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    calendars: Mapped[list["BusinessCalendar"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", passive_deletes=True
    )


class BusinessCalendar(Base):
    __tablename__ = "business_calendars"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "google_connection_id"],
            ["google_connections.tenant_id", "google_connections.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "google_connection_id", "google_calendar_id"),
        Index(
            "ix_business_calendars_one_selected",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_selected"),
        ),
        CheckConstraint(
            "NOT is_selected OR access_role IN ('owner', 'writer')",
            name="selected_calendar_writable",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    google_connection_id: Mapped[UUID] = mapped_column()
    google_calendar_id: Mapped[str] = mapped_column(String(1024))
    calendar_name: Mapped[str] = mapped_column(String(1024))
    timezone: Mapped[str] = mapped_column(String(100))
    access_role: Mapped[str] = mapped_column(String(30))
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    connection: Mapped[GoogleConnection] = relationship(back_populates="calendars")


class GoogleOAuthState(Base):
    __tablename__ = "google_oauth_states"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("auth_sessions.id", ondelete="CASCADE"))
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    encrypted_code_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
