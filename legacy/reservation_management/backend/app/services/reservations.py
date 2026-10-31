from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.core.domain import BookingError
from app.models import Customer, Offering, OutboxEvent, Reservation, ReservationResource
from app.repositories.booking import BookingRepository
from app.schemas.booking import AssignWrite, RescheduleWrite, ReservationProfile, ReservationWrite
from app.services.availability import AvailabilityService
from app.services.booking_policies import resolve_window
from app.services.calendar_payload import calendar_body

BLOCKING = {"PENDING", "CONFIRMED"}


class ReservationService:
    def __init__(self, db: Session, context: TenantContext):
        self.db = db
        self.context = context
        self.repo = BookingRepository(db, context.tenant.id)
        self.repo.lock()
        self.availability = AvailabilityService(db, context)

    def profile(self, row: Reservation) -> ReservationProfile:
        values = {
            key: getattr(row, key)
            for key in ReservationProfile.model_fields
            if key != "resource_ids"
        }
        ids = list(
            self.db.scalars(
                select(ReservationResource.resource_id)
                .where(
                    ReservationResource.tenant_id == row.tenant_id,
                    ReservationResource.reservation_id == row.id,
                )
                .order_by(ReservationResource.resource_id)
            )
        )
        return ReservationProfile(**values, resource_ids=ids)

    def enqueue(self, row: Reservation, offering: Offering) -> None:
        row.calendar_sync_status = "PENDING"
        self.db.add(
            OutboxEvent(
                tenant_id=row.tenant_id,
                reservation_id=row.id,
                event_type="calendar.delete" if row.status == "CANCELLED" else "calendar.upsert",
                idempotency_key=f"{row.id}:{row.sync_version}",
                payload={
                    "version": row.sync_version,
                    "body": calendar_body(row, offering, self.context.tenant),
                },
            )
        )
        self.db.flush()

    def save(self, data: ReservationWrite, identifier: UUID | None = None) -> Reservation:
        row = (
            self.repo.get(Reservation, identifier, lock=True)
            if identifier
            else Reservation(tenant_id=self.context.tenant.id)
        )
        if identifier and data.expected_version and data.expected_version != row.sync_version:
            raise BookingError("Reservation changed. Reload before saving")
        if identifier and row.status not in BLOCKING and data.status in BLOCKING:
            raise BookingError(
                "Finished or cancelled reservations cannot be reopened; create a new reservation"
            )
        if not identifier and data.status not in BLOCKING:
            raise BookingError("New reservations must be pending or confirmed", 422)
        self.repo.get(Customer, data.customer_id)
        offering = self.repo.get(Offering, data.offering_id)
        if data.status in BLOCKING:
            offering, window, resources = self.availability.prepare(data, identifier)
        else:
            window = resolve_window(data, offering, offering.policy, self.context.tenant.timezone)
            resources = []
        if identifier:
            self.db.execute(
                delete(ReservationResource).where(
                    ReservationResource.tenant_id == row.tenant_id,
                    ReservationResource.reservation_id == row.id,
                )
            )
            self.db.flush()
        row.customer_id = data.customer_id
        row.offering_id = data.offering_id
        row.start_at = window.start
        row.end_at = window.end
        row.local_start_date = window.local_start
        row.local_end_date = window.local_end
        row.party_size = data.party_size
        row.quantity = data.quantity
        row.status = data.status
        row.sync_version = (row.sync_version + 1) if identifier else 1
        if not identifier:
            row.calendar_sync_status = "PENDING"
        self.db.add(row)
        self.db.flush()
        from datetime import timedelta

        for resource in resources:
            self.db.add(
                ReservationResource(
                    tenant_id=row.tenant_id,
                    reservation_id=row.id,
                    resource_id=resource.id,
                    allocated_quantity=data.quantity if resource.allocation_mode == "SHARED" else 1,
                    allocation_status="ACTIVE",
                    start_at=row.start_at,
                    end_at=row.end_at,
                    occupied_until=row.end_at
                    + timedelta(minutes=offering.policy.turnaround_minutes),
                    exclusive=resource.allocation_mode == "EXCLUSIVE",
                )
            )
        self.db.flush()
        self.enqueue(row, offering)
        return row

    def data(self, row: Reservation) -> dict[str, object]:
        values = {
            key: getattr(row, key)
            for key in ("customer_id", "offering_id", "party_size", "quantity", "status")
        }
        if row.local_start_date:
            values.update(local_start_date=row.local_start_date, local_end_date=row.local_end_date)
        else:
            values.update(start_at=row.start_at, end_at=row.end_at)
        values["resource_ids"] = self.profile(row).resource_ids
        return values

    def reschedule(self, identifier: UUID, data: RescheduleWrite) -> Reservation:
        row = self.repo.get(Reservation, identifier, lock=True)
        if row.status not in BLOCKING:
            raise BookingError("Only pending or confirmed reservations can be rescheduled")
        values = self.data(row)
        for key in ("start_at", "end_at", "local_start_date", "local_end_date"):
            values.pop(key, None)
        values.update(data.model_dump(exclude_none=True))
        return self.save(ReservationWrite.model_validate(values), identifier)

    def assign(self, identifier: UUID, data: AssignWrite) -> Reservation:
        row = self.repo.get(Reservation, identifier, lock=True)
        values = self.data(row)
        values.update(data.model_dump())
        return self.save(ReservationWrite.model_validate(values), identifier)

    def change_status(
        self, identifier: UUID, status: str, expected: int | None = None
    ) -> Reservation:
        row = self.repo.get(Reservation, identifier, lock=True)
        if expected and row.sync_version != expected:
            raise BookingError("Reservation changed. Reload before saving")
        if status == row.status:
            return row
        if row.status not in BLOCKING:
            raise BookingError("Finished or cancelled reservations cannot change status")
        row.status = status
        row.sync_version += 1
        for allocation in self.db.scalars(
            select(ReservationResource).where(
                ReservationResource.tenant_id == row.tenant_id,
                ReservationResource.reservation_id == row.id,
            )
        ):
            allocation.allocation_status = "ACTIVE" if status in BLOCKING else "RELEASED"
        self.db.flush()
        self.enqueue(row, self.repo.get(Offering, row.offering_id))
        return row

    def remove(self, identifier: UUID) -> None:
        row = self.repo.get(Reservation, identifier, lock=True)
        if row.status in BLOCKING:
            row = self.change_status(identifier, "CANCELLED")
        row.deleted_at = datetime.now(UTC)

    def retry(self, identifier: UUID) -> None:
        row = self.repo.get(Reservation, identifier, lock=True)
        event = self.db.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.tenant_id == row.tenant_id,
                OutboxEvent.reservation_id == row.id,
                OutboxEvent.idempotency_key == f"{row.id}:{row.sync_version}",
            )
            .with_for_update()
        )
        if event is None:
            raise BookingError("No synchronization event is available", 404)
        if event.status == "PROCESSING":
            raise BookingError("Synchronization is in progress")
        event.status = "PENDING"
        event.attempts = 0
        event.last_error = None
        event.next_attempt_at = datetime.now(UTC)
        row.calendar_sync_status = "PENDING"

    def list(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        status: str | None = None,
        offering_id: UUID | None = None,
        resource_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
        local_from: date | None = None,
        local_until: date | None = None,
    ) -> list[Reservation]:
        from app.services.booking_policies import local_instant

        if local_from:
            if start:
                raise BookingError("Choose an offset timestamp or a local date filter", 422)
            start = local_instant(
                datetime.combine(local_from, datetime.min.time()), self.context.tenant.timezone
            )
        if local_until:
            if end:
                raise BookingError("Choose an offset timestamp or a local date filter", 422)
            end = local_instant(
                datetime.combine(local_until, datetime.min.time()), self.context.tenant.timezone
            )
        query = select(Reservation).where(
            Reservation.tenant_id == self.context.tenant.id, Reservation.deleted_at.is_(None)
        )
        if start and end and end <= start:
            raise BookingError("Date range must end after it starts", 422)
        if start:
            query = query.where(Reservation.end_at > start)
        if end:
            query = query.where(Reservation.start_at < end)
        if status:
            query = query.where(Reservation.status == status)
        if offering_id:
            self.repo.get(Offering, offering_id)
            query = query.where(Reservation.offering_id == offering_id)
        if resource_id:
            from app.models import Resource

            self.repo.get(Resource, resource_id)
            query = query.join(
                ReservationResource, ReservationResource.reservation_id == Reservation.id
            ).where(ReservationResource.resource_id == resource_id)
        return list(
            self.db.scalars(
                query.order_by(Reservation.start_at, Reservation.id).limit(limit).offset(offset)
            )
        )
