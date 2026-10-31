from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.core.domain import BookingError
from app.models import Customer, Offering, Resource, WaitlistEntry
from app.repositories.booking import BookingRepository
from app.schemas.booking import WaitlistWrite
from app.services.availability import AvailabilityService
from app.services.booking_policies import local_instant


class WaitlistService:
    def __init__(self, db: Session, context: TenantContext):
        self.db = db
        self.context = context
        self.repo = BookingRepository(db, context.tenant.id)
        self.repo.lock()

    def save(self, data: WaitlistWrite, identifier: UUID | None = None) -> WaitlistEntry:
        self.repo.get(Customer, data.customer_id)
        offering = self.repo.get(Offering, data.offering_id)
        if not offering.active:
            raise BookingError("Offering is inactive")
        if data.preferred_resource_id:
            resource = self.repo.get(Resource, data.preferred_resource_id)
            compatible = AvailabilityService(self.db, self.context).compatible(offering.id)
            if resource not in compatible:
                raise BookingError("Preferred resource is inactive or incompatible", 422)
            if data.party_size and resource.capacity and data.party_size > resource.capacity:
                raise BookingError("Party exceeds preferred resource capacity", 422)
        has_dates = bool(data.local_start_date or data.local_end_date)
        has_available = bool(data.available_from or data.available_until)
        has_offsets = bool(data.start_at or data.end_at)
        has_local = bool(data.local_start_at or data.local_end_at)
        if sum((has_dates, has_available, has_offsets, has_local)) > 1:
            raise BookingError("Choose exactly one waiting-list date/time representation", 422)
        if offering.booking_mode == "NIGHTLY" and not has_dates:
            raise BookingError("Nightly preferences require local stay dates", 422)
        if offering.booking_mode != "NIGHTLY" and has_dates:
            raise BookingError("Local stay dates require a nightly offering", 422)
        first: datetime | None
        last: datetime | None
        if offering.booking_mode == "NIGHTLY":
            if (
                not data.local_start_date
                or not data.local_end_date
                or data.local_end_date <= data.local_start_date
            ):
                raise BookingError(
                    "Nightly preferences require a check-in/check-out date range", 422
                )
            first = local_instant(
                datetime.combine(data.local_start_date, offering.policy.check_in_time),
                self.context.tenant.timezone,
            )
            last = local_instant(
                datetime.combine(data.local_end_date, offering.policy.check_out_time),
                self.context.tenant.timezone,
            )
        else:
            first = (
                data.available_from
                or data.start_at
                or (
                    local_instant(data.local_start_at, self.context.tenant.timezone)
                    if data.local_start_at
                    else None
                )
            )
            last = (
                data.available_until
                or data.end_at
                or (
                    local_instant(data.local_end_at, self.context.tenant.timezone)
                    if data.local_end_at
                    else None
                )
            )
        if first is None or last is None or last <= first:
            raise BookingError("Waiting-list availability must end after it starts", 422)
        if offering.capacity_limit and data.quantity > offering.capacity_limit:
            raise BookingError("Requested quantity exceeds offering capacity", 422)
        row = (
            self.repo.get(WaitlistEntry, identifier)
            if identifier
            else WaitlistEntry(tenant_id=self.context.tenant.id)
        )
        for key in (
            "customer_id",
            "offering_id",
            "party_size",
            "quantity",
            "status",
            "preferred_resource_id",
            "local_start_date",
            "local_end_date",
        ):
            setattr(row, key, getattr(data, key))
        row.available_from = first
        row.available_until = last
        self.db.add(row)
        self.db.flush()
        return row
