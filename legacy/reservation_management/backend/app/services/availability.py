from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.core.domain import BookingError
from app.models import (
    Offering,
    OfferingResource,
    Reservation,
    ReservationResource,
    Resource,
    ResourceRequirement,
)
from app.repositories.booking import BookingRepository
from app.schemas.booking import AvailabilityProfile, BookingInput, ResourceProfile
from app.services.allocation import (
    AllocationStrategy,
    ExplicitAllocation,
    GreedyCompatibleAllocation,
)
from app.services.booking_policies import BookingWindow, peak_usage, resolve_window


class AvailabilityService:
    def __init__(
        self, db: Session, context: TenantContext, strategy: AllocationStrategy | None = None
    ):
        self.strategy = strategy or GreedyCompatibleAllocation()
        self.db = db
        self.context = context
        self.repo = BookingRepository(db, context.tenant.id)
        self.repo.lock()

    def compatible(self, offering_id: UUID) -> list[Resource]:
        self.repo.get(Offering, offering_id)
        return list(
            self.db.scalars(
                select(Resource)
                .join(OfferingResource, OfferingResource.resource_id == Resource.id)
                .where(
                    Resource.tenant_id == self.context.tenant.id,
                    OfferingResource.tenant_id == self.context.tenant.id,
                    OfferingResource.offering_id == offering_id,
                    Resource.active.is_(True),
                )
                .order_by(Resource.id)
            )
        )

    def resource_available(
        self,
        resource: Resource,
        window: BookingWindow,
        quantity: int,
        party_size: int | None,
        ignore: UUID | None,
        turnaround: int,
    ) -> bool:
        if not resource.active or (
            party_size and resource.capacity and party_size > resource.capacity
        ):
            return False
        end = window.end + timedelta(minutes=turnaround)
        query = select(ReservationResource).where(
            ReservationResource.tenant_id == self.context.tenant.id,
            ReservationResource.resource_id == resource.id,
            ReservationResource.allocation_status == "ACTIVE",
            ReservationResource.start_at < end,
            ReservationResource.occupied_until > window.start,
        )
        if ignore:
            query = query.where(ReservationResource.reservation_id != ignore)
        rows = list(self.db.scalars(query))
        if resource.allocation_mode == "EXCLUSIVE":
            return not rows
        return peak_usage(
            [(row.start_at, row.occupied_until, row.allocated_quantity) for row in rows],
            window.start,
            end,
        ) + quantity <= (resource.capacity or 0)

    def prepare(
        self, data: BookingInput, ignore: UUID | None = None
    ) -> tuple[Offering, BookingWindow, list[Resource]]:
        offering = self.repo.get(Offering, data.offering_id)
        if not offering.active:
            raise BookingError("Offering is inactive")
        policy = offering.policy
        window = resolve_window(data, offering, policy, self.context.tenant.timezone)
        compatible = self.compatible(offering.id)
        self.db.execute(
            text("SELECT public.lock_booking_inventory(:offering,CAST(:resources AS uuid[]))"),
            {"offering": offering.id, "resources": [item.id for item in compatible]},
        )
        if offering.capacity_limit:
            query = select(Reservation).where(
                Reservation.tenant_id == self.context.tenant.id,
                Reservation.offering_id == offering.id,
                Reservation.status.in_(["PENDING", "CONFIRMED"]),
                Reservation.start_at < window.end,
                Reservation.end_at > window.start,
            )
            if ignore:
                query = query.where(Reservation.id != ignore)
            rows = list(self.db.scalars(query))
            if (
                peak_usage(
                    [(row.start_at, row.end_at, row.quantity) for row in rows],
                    window.start,
                    window.end,
                )
                + data.quantity
                > offering.capacity_limit
            ):
                raise BookingError("Offering capacity is exhausted during this period")
        available = [
            row
            for row in compatible
            if self.resource_available(
                row, window, data.quantity, data.party_size, ignore, policy.turnaround_minutes
            )
        ]
        requirements = list(
            self.db.scalars(
                select(ResourceRequirement)
                .where(
                    ResourceRequirement.tenant_id == offering.tenant_id,
                    ResourceRequirement.offering_id == offering.id,
                )
                .order_by(ResourceRequirement.resource_type)
            )
        )
        if data.resource_ids is not None:
            for identifier in data.resource_ids:
                self.repo.get(Resource, identifier)
            selected = ExplicitAllocation().select(
                compatible, available, requirements, data.resource_ids
            )
        else:
            selected = self.strategy.select(compatible, available, requirements, None)
        for requirement in self.db.scalars(
            select(ResourceRequirement).where(
                ResourceRequirement.tenant_id == offering.tenant_id,
                ResourceRequirement.offering_id == offering.id,
            )
        ):
            if (
                sum(row.resource_type == requirement.resource_type for row in selected)
                < requirement.required_count
            ):
                raise BookingError("Not all required resource types are available")
        if (policy.require_resources or compatible) and not selected:
            raise BookingError("No compatible available resource satisfies this reservation")
        return offering, window, selected

    def check(self, data: BookingInput) -> AvailabilityProfile:
        try:
            _, window, resources = self.prepare(data)
            return AvailabilityProfile(
                available=True,
                start_at=window.start,
                end_at=window.end,
                resources=[ResourceProfile.model_validate(row) for row in resources],
            )
        except BookingError as error:
            if error.status != 409:
                raise
            return AvailabilityProfile(available=False, reason=error.detail)
