"""Historical schema declarations for Alembic only; not an active reservation domain."""

from datetime import date, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TenantRecord:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Offering(TenantRecord, Timestamps, Base):
    __tablename__ = "offerings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_offering_tenant_id"),
        CheckConstraint("booking_mode IN ('TIMED','NIGHTLY','CAPACITY')", name="offering_mode"),
        CheckConstraint(
            "default_duration_minutes IS NULL OR default_duration_minutes > 0",
            name="offering_duration",
        ),
        CheckConstraint(
            "min_duration_minutes IS NULL OR min_duration_minutes > 0", name="offering_min_duration"
        ),
        CheckConstraint(
            "max_duration_minutes IS NULL OR "
            "max_duration_minutes >= coalesce(min_duration_minutes,1)",
            name="offering_max_duration",
        ),
        CheckConstraint("capacity_limit IS NULL OR capacity_limit > 0", name="offering_capacity"),
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    booking_mode: Mapped[str] = mapped_column(String(20))
    default_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    min_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    max_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    capacity_limit: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    resource_links: Mapped[list["OfferingResource"]] = relationship(viewonly=True)
    inventory_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    policy: Mapped["BookingPolicy"] = relationship(
        back_populates="offering", cascade="all, delete-orphan", uselist=False
    )


class BookingPolicy(TenantRecord, Base):
    __tablename__ = "booking_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["offerings.tenant_id", "offerings.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "offering_id", name="uq_policy_offering"),
        CheckConstraint(
            "min_nights > 0 AND (max_nights IS NULL OR max_nights >= min_nights)",
            name="policy_nights",
        ),
        CheckConstraint("turnaround_minutes >= 0", name="policy_turnaround"),
    )
    offering_id: Mapped[UUID] = mapped_column()
    require_resources: Mapped[bool] = mapped_column(Boolean, default=False)
    check_in_time: Mapped[time] = mapped_column(Time, default=time(15))
    check_out_time: Mapped[time] = mapped_column(Time, default=time(11))
    min_nights: Mapped[int] = mapped_column(Integer, default=1)
    max_nights: Mapped[int | None] = mapped_column(Integer)
    turnaround_minutes: Mapped[int] = mapped_column(Integer, default=0)
    offering: Mapped[Offering] = relationship(back_populates="policy")


class ResourceRequirement(TenantRecord, Base):
    __tablename__ = "resource_requirements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["offerings.tenant_id", "offerings.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "offering_id", "resource_type", name="uq_requirement_type"),
        CheckConstraint("required_count > 0", name="requirement_count"),
    )
    offering_id: Mapped[UUID] = mapped_column()
    resource_type: Mapped[str] = mapped_column(String(80))
    required_count: Mapped[int] = mapped_column(Integer, default=1)


class Resource(TenantRecord, Timestamps, Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_resource_tenant_id"),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="resource_capacity"),
        CheckConstraint("allocation_mode IN ('EXCLUSIVE','SHARED')", name="resource_mode"),
        CheckConstraint(
            "allocation_mode != 'SHARED' OR capacity IS NOT NULL", name="shared_capacity_required"
        ),
    )
    name: Mapped[str] = mapped_column(String(200))
    resource_type: Mapped[str] = mapped_column(String(80))
    capacity: Mapped[int | None] = mapped_column(Integer)
    inventory_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    allocation_mode: Mapped[str] = mapped_column(String(20), default="EXCLUSIVE")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OfferingResource(TenantRecord, Base):
    __tablename__ = "offering_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["offerings.tenant_id", "offerings.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resource_id"],
            ["resources.tenant_id", "resources.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "offering_id", "resource_id", name="uq_offering_resource"),
    )
    offering: Mapped[Offering] = relationship(viewonly=True)
    resource: Mapped[Resource] = relationship(viewonly=True)
    offering_id: Mapped[UUID] = mapped_column()
    resource_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Customer(TenantRecord, Timestamps, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customer_tenant_id"),
        UniqueConstraint("tenant_id", "email", name="uq_customer_email"),
    )
    reservations: Mapped[list["Reservation"]] = relationship(viewonly=True)
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    notification_consent: Mapped[bool] = mapped_column(Boolean, default=False)


class Reservation(TenantRecord, Timestamps, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "(local_start_date IS NULL) = (local_end_date IS NULL)", name="reservation_date_pair"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_reservation_tenant_id"),
        UniqueConstraint("tenant_id", "id", "start_at", "end_at", name="uq_reservation_interval"),
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "offering_id"], ["offerings.tenant_id", "offerings.id"]),
        CheckConstraint("end_at > start_at", name="reservation_interval"),
        CheckConstraint(
            "quantity > 0 AND (party_size IS NULL OR party_size > 0)", name="reservation_quantity"
        ),
        CheckConstraint(
            "status IN ('PENDING','CONFIRMED','CANCELLED','COMPLETED','NO_SHOW')",
            name="reservation_status",
        ),
        CheckConstraint(
            "calendar_sync_status IN ('PENDING','SYNCED','FAILED','CONFLICT','NOT_CONFIGURED')",
            name="reservation_sync_status",
        ),
        CheckConstraint(
            "(local_start_date IS NULL AND local_end_date IS NULL) "
            "OR local_end_date > local_start_date",
            name="reservation_local_dates",
        ),
        Index("ix_reservations_tenant_period", "tenant_id", "start_at", "end_at"),
    )
    customer: Mapped[Customer] = relationship(viewonly=True)
    offering: Mapped[Offering] = relationship(viewonly=True)
    outbox_events: Mapped[list["OutboxEvent"]] = relationship(viewonly=True)
    customer_id: Mapped[UUID] = mapped_column()
    offering_id: Mapped[UUID] = mapped_column()
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_start_date: Mapped[date | None] = mapped_column(Date)
    local_end_date: Mapped[date | None] = mapped_column(Date)
    party_size: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    google_calendar_id: Mapped[str | None] = mapped_column(String(1024))
    google_event_id: Mapped[str | None] = mapped_column(String(128))
    google_subject: Mapped[str | None] = mapped_column(String(255))
    google_event_etag: Mapped[str | None] = mapped_column(String(255))
    calendar_sync_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    sync_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocations: Mapped[list["ReservationResource"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan", passive_deletes=True
    )


class ReservationResource(TenantRecord, Base):
    __tablename__ = "reservation_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "reservation_id", "start_at", "end_at"],
            [
                "reservations.tenant_id",
                "reservations.id",
                "reservations.start_at",
                "reservations.end_at",
            ],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "resource_id"], ["resources.tenant_id", "resources.id"]),
        UniqueConstraint(
            "tenant_id", "reservation_id", "resource_id", name="uq_reservation_resource"
        ),
        CheckConstraint("allocated_quantity > 0 AND end_at > start_at", name="allocation_quantity"),
        CheckConstraint("allocation_status IN ('ACTIVE','RELEASED')", name="allocation_status"),
        ExcludeConstraint(
            ("tenant_id", "="),
            ("resource_id", "="),
            (func.tstzrange(column("start_at"), column("occupied_until"), "[)"), "&&"),
            where=text("allocation_status = 'ACTIVE' AND exclusive"),
            using="gist",
            name="excl_exclusive_resource_overlap",
        ),
    )
    reservation_id: Mapped[UUID] = mapped_column()
    resource_id: Mapped[UUID] = mapped_column()
    allocated_quantity: Mapped[int] = mapped_column(Integer, default=1)
    allocation_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    reservation: Mapped[Reservation] = relationship(back_populates="allocations")


class WaitlistEntry(TenantRecord, Timestamps, Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        ForeignKeyConstraint(["tenant_id", "offering_id"], ["offerings.tenant_id", "offerings.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "preferred_resource_id"], ["resources.tenant_id", "resources.id"]
        ),
        CheckConstraint("available_until > available_from", name="waitlist_interval"),
        CheckConstraint(
            "quantity > 0 AND (party_size IS NULL OR party_size > 0)", name="waitlist_quantity"
        ),
        CheckConstraint("status IN ('ACTIVE','INACTIVE','FULFILLED')", name="waitlist_status"),
    )
    customer: Mapped[Customer] = relationship(viewonly=True)
    offering: Mapped[Offering] = relationship(viewonly=True)
    preferred_resource: Mapped[Resource | None] = relationship(viewonly=True)
    customer_id: Mapped[UUID] = mapped_column()
    offering_id: Mapped[UUID] = mapped_column()
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_start_date: Mapped[date | None] = mapped_column(Date)
    local_end_date: Mapped[date | None] = mapped_column(Date)
    preferred_resource_id: Mapped[UUID | None] = mapped_column()
    party_size: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class OutboxEvent(TenantRecord, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "reservation_id"], ["reservations.tenant_id", "reservations.id"]
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_idempotency"),
        Index("ix_outbox_due", "status", "next_attempt_at"),
        CheckConstraint("attempts >= 0", name="outbox_attempts"),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','PROCESSED','FAILED','CONFLICT','SUPERSEDED')",
            name="outbox_status",
        ),
    )
    reservation_id: Mapped[UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(150))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    last_error: Mapped[str | None] = mapped_column(String(100))
