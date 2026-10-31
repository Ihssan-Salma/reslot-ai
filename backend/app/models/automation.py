from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenantIntegrationRecord:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SpreadsheetConfiguration(TenantIntegrationRecord, Base):
    __tablename__ = "spreadsheet_configurations"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "google_connection_id"],
            ["google_connections.tenant_id", "google_connections.id"],
        ),
    )
    google_connection_id: Mapped[UUID] = mapped_column()
    google_subject: Mapped[str] = mapped_column(String(255))
    spreadsheet_id: Mapped[str] = mapped_column(String(200))


class CalendarEventSnapshot(TenantIntegrationRecord, Base):
    __tablename__ = "calendar_event_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "calendar_id", "event_id", name="uq_snapshot_source_event"),
    )
    calendar_id: Mapped[str] = mapped_column(String(1024))
    event_id: Mapped[str] = mapped_column(String(1024))
    google_subject: Mapped[str] = mapped_column(String(255))
    etag: Mapped[str | None] = mapped_column(String(255))
    classification: Mapped[str] = mapped_column(String(20))
    source_status: Mapped[str] = mapped_column(String(30))
    normalized_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    review_reasons: Mapped[list[str]] = mapped_column(JSONB)


class IntegrationCursor(TenantIntegrationRecord, Base):
    __tablename__ = "integration_cursors"
    __table_args__ = (UniqueConstraint("tenant_id", "source", "scope_key", name="uq_cursor_scope"),)
    source: Mapped[str] = mapped_column(String(30))
    scope_key: Mapped[str] = mapped_column(String(200))
    encrypted_cursor: Mapped[str | None] = mapped_column(Text)


class WorkflowState(TenantIntegrationRecord, Base):
    __tablename__ = "workflow_states"
    __table_args__ = (UniqueConstraint("tenant_id", "workflow_key", name="uq_workflow_key"),)
    workflow_key: Mapped[str] = mapped_column(String(200))
    workflow_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)


class IdempotencyRecord(TenantIntegrationRecord, Base):
    __tablename__ = "integration_idempotency"
    __table_args__ = (
        UniqueConstraint("tenant_id", "operation_key", name="uq_integration_operation"),
        CheckConstraint(
            "status IN ('IN_PROGRESS','COMPLETED','FAILED','REVIEW')", name="idempotency_status"
        ),
    )
    operation_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
