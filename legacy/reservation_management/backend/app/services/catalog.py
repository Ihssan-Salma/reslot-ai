from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.core.domain import BookingError
from app.models import (
    BookingPolicy,
    Customer,
    Offering,
    OfferingResource,
    Reservation,
    ReservationResource,
    Resource,
    ResourceRequirement,
    WaitlistEntry,
)
from app.repositories.booking import BookingRepository
from app.schemas.booking import (
    BusinessUpdate,
    CompatibilityWrite,
    CustomerWrite,
    OfferingProfile,
    OfferingWrite,
    PolicyWrite,
    RequirementWrite,
    ResourceWrite,
)


class CatalogService:
    def __init__(self, db: Session, context: TenantContext):
        self.db = db
        self.context = context
        self.repo = BookingRepository(db, context.tenant.id)
        self.repo.lock()

    def manager(self) -> None:
        if self.context.membership.role not in {"owner", "admin"}:
            raise BookingError(
                "Only business owners and admins can configure the booking catalog", 403
            )

    def offering_profile(self, row: Offering) -> OfferingProfile:
        policy = row.policy
        requirements = list(
            self.db.scalars(
                select(ResourceRequirement).where(
                    ResourceRequirement.tenant_id == self.context.tenant.id,
                    ResourceRequirement.offering_id == row.id,
                )
            )
        )
        values = {key: getattr(row, key) for key in OfferingWrite.model_fields if key != "policy"}
        return OfferingProfile(
            **values,
            id=row.id,
            tenant_id=row.tenant_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            policy=PolicyWrite(
                **{
                    key: getattr(policy, key)
                    for key in PolicyWrite.model_fields
                    if key != "requirements"
                },
                requirements=[
                    RequirementWrite(
                        resource_type=item.resource_type, required_count=item.required_count
                    )
                    for item in requirements
                ],
            ),
        )

    def save_offering(self, data: OfferingWrite, identifier: UUID | None = None) -> Offering:
        self.manager()
        row = (
            self.repo.get(Offering, identifier, lock=True)
            if identifier
            else Offering(tenant_id=self.context.tenant.id)
        )
        if identifier:
            old = self.offering_profile(row)
            important = {
                "booking_mode",
                "default_duration_minutes",
                "min_duration_minutes",
                "max_duration_minutes",
                "capacity_limit",
                "policy",
            }
            changed = any(getattr(old, key) != getattr(data, key) for key in important)
            if changed and self.db.scalar(
                select(Reservation.id)
                .where(
                    Reservation.offering_id == row.id,
                    Reservation.tenant_id == row.tenant_id,
                    Reservation.status.in_(["PENDING", "CONFIRMED"]),
                )
                .limit(1)
            ):
                raise BookingError("Finish active reservations before changing booking rules")
        for key, value in data.model_dump(exclude={"policy"}).items():
            setattr(row, key, value)
        self.db.add(row)
        self.db.flush()
        policy = row.policy if identifier else None
        if policy is None:
            policy = BookingPolicy(tenant_id=row.tenant_id, offering_id=row.id)
            self.db.add(policy)
            row.policy = policy
        for key, value in data.policy.model_dump(exclude={"requirements"}).items():
            setattr(policy, key, value)
        self.db.execute(
            delete(ResourceRequirement).where(
                ResourceRequirement.tenant_id == row.tenant_id,
                ResourceRequirement.offering_id == row.id,
            )
        )
        for item in data.policy.requirements:
            self.db.add(
                ResourceRequirement(
                    tenant_id=row.tenant_id, offering_id=row.id, **item.model_dump()
                )
            )
        self.db.flush()
        return row

    def save_resource(self, data: ResourceWrite, identifier: UUID | None = None) -> Resource:
        self.manager()
        row = (
            self.repo.get(Resource, identifier, lock=True)
            if identifier
            else Resource(tenant_id=self.context.tenant.id)
        )
        if identifier and any(
            getattr(row, key) != getattr(data, key)
            for key in ("capacity", "allocation_mode", "resource_type")
        ):
            if self.db.scalar(
                select(ReservationResource.id)
                .where(
                    ReservationResource.tenant_id == row.tenant_id,
                    ReservationResource.resource_id == row.id,
                    ReservationResource.allocation_status == "ACTIVE",
                )
                .limit(1)
            ):
                raise BookingError("Release active allocations before changing resource rules")
        for key, value in data.model_dump().items():
            setattr(row, key, value)
        self.db.add(row)
        self.db.flush()
        return row

    def save_customer(self, data: CustomerWrite, identifier: UUID | None = None) -> Customer:
        row = (
            self.repo.get(Customer, identifier)
            if identifier
            else Customer(tenant_id=self.context.tenant.id)
        )
        for key, value in data.model_dump().items():
            setattr(row, key, value)
        self.db.add(row)
        self.db.flush()
        return row

    def compatibility(self, data: CompatibilityWrite) -> OfferingResource:
        self.manager()
        self.repo.get(Offering, data.offering_id)
        self.repo.get(Resource, data.resource_id)
        row = OfferingResource(tenant_id=self.context.tenant.id, **data.model_dump())
        self.db.add(row)
        self.db.flush()
        return row

    def remove_compatibility(self, identifier: UUID) -> None:
        self.manager()
        row = self.repo.get(OfferingResource, identifier)
        if self.db.scalar(
            select(ReservationResource.id)
            .join(Reservation, Reservation.id == ReservationResource.reservation_id)
            .where(
                ReservationResource.tenant_id == row.tenant_id,
                ReservationResource.resource_id == row.resource_id,
                Reservation.offering_id == row.offering_id,
                ReservationResource.allocation_status == "ACTIVE",
            )
            .limit(1)
        ):
            raise BookingError("Compatibility is used by active reservations")
        self.db.delete(row)

    def delete_customer(self, identifier: UUID) -> None:
        row = self.repo.get(Customer, identifier)
        for model in (Reservation, WaitlistEntry):
            if self.db.scalar(
                select(model.id)
                .where(model.tenant_id == row.tenant_id, model.customer_id == row.id)
                .limit(1)
            ):
                raise BookingError(
                    "Customer has reservation or waiting-list history and cannot be deleted"
                )
        self.db.delete(row)

    def update_business(self, data: BusinessUpdate) -> None:
        self.manager()
        tenant = self.context.tenant
        if tenant.timezone != data.timezone and self.db.scalar(
            select(Reservation.id)
            .where(
                Reservation.tenant_id == tenant.id, Reservation.status.in_(["PENDING", "CONFIRMED"])
            )
            .limit(1)
        ):
            raise BookingError(
                "Finish or cancel active reservations before changing the business timezone"
            )
        tenant.business_type = data.business_type
        tenant.timezone = data.timezone
        self.db.flush()
