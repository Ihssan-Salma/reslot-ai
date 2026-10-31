from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.db.session import set_tenant_context, set_user_context
from app.integrations.google.calendar_writer import CalendarWriter
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP
from app.models import (
    BusinessCalendar,
    OutboxEvent,
    Reservation,
    Tenant,
    TenantMembership,
)
from app.services.google import GoogleService


def claim_work(
    engine: Engine, limit: int = 10, event_id: UUID | None = None
) -> list[dict[str, Any]]:
    with engine.begin() as connection:
        query = (
            "SELECT * FROM public.claim_calendar_item(:identifier)"
            if event_id
            else "SELECT * FROM public.claim_calendar_work(:limit)"
        )
        parameters = {"identifier": event_id} if event_id else {"limit": limit}
        rows = connection.execute(text(query), parameters).mappings().all()
        return [dict(row) for row in rows]


def authorized_context(db: Session, claim: dict[str, Any]) -> TenantContext:
    tenant_id = UUID(str(claim["tenant_id"]))
    user_id = UUID(str(claim["user_id"]))
    set_user_context(db, user_id)
    set_tenant_context(db, tenant_id)
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.role.in_(["owner", "admin"]),
        )
    )
    tenant = db.get(Tenant, tenant_id)
    if membership is None or tenant is None or tenant.status != "active":
        raise GoogleError(
            "tenant_authorization_changed", "A current business owner/admin is required", 403
        )
    return TenantContext(tenant, membership)


def locked_work(db: Session, claim: dict[str, Any]) -> tuple[OutboxEvent, Reservation] | None:
    # Match API lock order: reservation before outbox, avoiding retry/worker deadlocks.
    identifier = UUID(str(claim["event_id"]))
    initial = db.get(OutboxEvent, identifier)
    if initial is None:
        return None
    reservation = db.scalar(
        select(Reservation)
        .where(Reservation.id == initial.reservation_id, Reservation.tenant_id == initial.tenant_id)
        .with_for_update()
    )
    event = db.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.id == identifier, OutboxEvent.tenant_id == initial.tenant_id)
        .with_for_update()
    )
    if (
        reservation is None
        or event is None
        or event.status != "PROCESSING"
        or event.lease_token != claim["lease_token"]
    ):
        return None
    if event.payload.get("version") != reservation.sync_version:
        event.status = "SUPERSEDED"
        event.processed_at = datetime.now(UTC)
        return None
    return event, reservation


def failed(event: OutboxEvent, reservation: Reservation, error: GoogleError) -> None:
    conflict = error.code in {"calendar_external_conflict", "google_identity_changed"}
    event.status = "CONFLICT" if conflict else "FAILED"
    event.last_error = error.code
    event.next_attempt_at = datetime.now(UTC) + timedelta(
        seconds=min(900, 5 * (2 ** min(event.attempts, 8)))
    )
    event.lease_expires_at = None
    event.lease_token = None
    reservation.calendar_sync_status = (
        "CONFLICT"
        if conflict
        else "NOT_CONFIGURED"
        if error.code in {"google_not_configured", "calendar_not_selected"}
        else "FAILED"
    )


def process_claim(engine: Engine, claim: dict[str, Any], http: GoogleHTTP) -> None:
    # Persist the target before any external write. Retries never switch calendars after a crash.
    with Session(engine, expire_on_commit=False) as db, db.begin():
        context = authorized_context(db, claim)
        work = locked_work(db, claim)
        if work is None:
            return
        event, reservation = work
        if reservation.status == "CANCELLED" and reservation.google_calendar_id is None:
            event.status = "PROCESSED"
            event.processed_at = datetime.now(UTC)
            reservation.calendar_sync_status = "SYNCED"
            return
        try:
            google = GoogleService(db, context, http)
            connection = google.connected()
            if reservation.google_calendar_id is None:
                selected = db.scalar(
                    select(BusinessCalendar).where(
                        BusinessCalendar.tenant_id == context.tenant.id,
                        BusinessCalendar.google_connection_id == connection.id,
                        BusinessCalendar.is_selected.is_(True),
                    )
                )
                if selected is None:
                    raise GoogleError(
                        "calendar_not_selected",
                        "Select a writable calendar before retrying synchronization",
                        409,
                    )
                reservation.google_calendar_id = selected.google_calendar_id
                reservation.google_subject = connection.google_subject
                reservation.google_event_id = reservation.id.hex
            elif reservation.google_subject != connection.google_subject:
                raise GoogleError(
                    "google_identity_changed",
                    "The reservation belongs to a different Google account",
                    409,
                )
        except GoogleError as error:
            failed(event, reservation, error)
            return
    with Session(engine, expire_on_commit=False) as db, db.begin():
        context = authorized_context(db, claim)
        work = locked_work(db, claim)
        if work is None:
            return
        event, reservation = work
        try:
            google = GoogleService(db, context, http)
            if google.connected().google_subject != reservation.google_subject:
                raise GoogleError(
                    "google_identity_changed",
                    "The reservation belongs to a different Google account",
                    409,
                )
            body = event.payload.get("body")
            if not isinstance(body, dict):
                raise GoogleError("invalid_snapshot", "Synchronization snapshot is invalid")
            prior: list[dict[str, Any]] = []
            # An earlier snapshot recovers safely from a crash after Google accepted a write.
            older = db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.tenant_id == reservation.tenant_id,
                    OutboxEvent.reservation_id == reservation.id,
                    OutboxEvent.id != event.id,
                )
                .order_by(OutboxEvent.created_at.desc())
            )
            for previous in older:
                previous_body = previous.payload.get("body")
                if isinstance(previous_body, dict):
                    prior.append(previous_body)
            writer = CalendarWriter(google, reservation.google_calendar_id or "")
            etag = writer.synchronize(
                reservation.google_event_id or reservation.id.hex,
                body,
                reservation.status == "CANCELLED",
                reservation.google_event_etag,
                prior,
            )
            reservation.google_event_etag = etag
            reservation.calendar_sync_status = "SYNCED"
            event.status = "PROCESSED"
            event.processed_at = datetime.now(UTC)
            event.last_error = None
            event.lease_token = None
            event.lease_expires_at = None
        except GoogleError as error:
            failed(event, reservation, error)
