from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from app.api.dependencies import CurrentTenant, Db
from app.api.routes.tenants import tenant_profile
from app.core.security import require_csrf
from app.models import (
    Customer,
    Offering,
    OfferingResource,
    OutboxEvent,
    Reservation,
    Resource,
    WaitlistEntry,
)
from app.schemas.auth import TenantProfile
from app.schemas.booking import (
    AssignWrite,
    AvailabilityProfile,
    BookingInput,
    BusinessUpdate,
    CompatibilityProfile,
    CompatibilityWrite,
    CustomerProfile,
    CustomerWrite,
    OfferingProfile,
    OfferingWrite,
    OutboxProfile,
    PeriodsInput,
    RescheduleWrite,
    ReservationProfile,
    ReservationWrite,
    ResourceProfile,
    ResourceWrite,
    Status,
    StatusWrite,
    WaitlistProfile,
    WaitlistWrite,
)
from app.services.availability import AvailabilityService
from app.services.catalog import CatalogService
from app.services.periods import available_periods
from app.services.reservations import ReservationService
from app.services.waitlist import WaitlistService

router = APIRouter(prefix="/api/v1/booking", tags=["reservation management"])
CSRF = [Depends(require_csrf)]


@router.get("/business", response_model=TenantProfile)
def business(context: CurrentTenant) -> TenantProfile:
    return tenant_profile(context)


@router.put("/business", response_model=TenantProfile, dependencies=CSRF)
def update_business(data: BusinessUpdate, context: CurrentTenant, db: Db) -> TenantProfile:
    CatalogService(db, context).update_business(data)
    return tenant_profile(context)


@router.get("/offerings", response_model=list[OfferingProfile])
def offerings(
    context: CurrentTenant,
    db: Db,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[OfferingProfile]:
    service = CatalogService(db, context)
    return [service.offering_profile(row) for row in service.repo.list(Offering, limit, offset)]


@router.post("/offerings", response_model=OfferingProfile, status_code=201, dependencies=CSRF)
def create_offering(data: OfferingWrite, context: CurrentTenant, db: Db) -> OfferingProfile:
    service = CatalogService(db, context)
    return service.offering_profile(service.save_offering(data))


@router.get("/offerings/{identifier}", response_model=OfferingProfile)
def offering(identifier: UUID, context: CurrentTenant, db: Db) -> OfferingProfile:
    service = CatalogService(db, context)
    return service.offering_profile(service.repo.get(Offering, identifier))


@router.put("/offerings/{identifier}", response_model=OfferingProfile, dependencies=CSRF)
def update_offering(
    identifier: UUID, data: OfferingWrite, context: CurrentTenant, db: Db
) -> OfferingProfile:
    service = CatalogService(db, context)
    return service.offering_profile(service.save_offering(data, identifier))


@router.delete("/offerings/{identifier}", status_code=204, dependencies=CSRF)
def deactivate_offering(identifier: UUID, context: CurrentTenant, db: Db) -> None:
    service = CatalogService(db, context)
    service.manager()
    service.repo.get(Offering, identifier).active = False


@router.get("/resources", response_model=list[ResourceProfile])
def resources(
    context: CurrentTenant,
    db: Db,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Resource]:
    return CatalogService(db, context).repo.list(Resource, limit, offset)


@router.post("/resources", response_model=ResourceProfile, status_code=201, dependencies=CSRF)
def create_resource(data: ResourceWrite, context: CurrentTenant, db: Db) -> Resource:
    return CatalogService(db, context).save_resource(data)


@router.get("/resources/{identifier}", response_model=ResourceProfile)
def resource(identifier: UUID, context: CurrentTenant, db: Db) -> Resource:
    return CatalogService(db, context).repo.get(Resource, identifier)


@router.put("/resources/{identifier}", response_model=ResourceProfile, dependencies=CSRF)
def update_resource(
    identifier: UUID, data: ResourceWrite, context: CurrentTenant, db: Db
) -> Resource:
    return CatalogService(db, context).save_resource(data, identifier)


@router.delete("/resources/{identifier}", status_code=204, dependencies=CSRF)
def deactivate_resource(identifier: UUID, context: CurrentTenant, db: Db) -> None:
    service = CatalogService(db, context)
    service.manager()
    service.repo.get(Resource, identifier).active = False


@router.get("/offerings/{identifier}/compatible-resources", response_model=list[ResourceProfile])
def compatible_resources(identifier: UUID, context: CurrentTenant, db: Db) -> list[Resource]:
    return AvailabilityService(db, context).compatible(identifier)


@router.get("/offering-resources", response_model=list[CompatibilityProfile])
def compatibilities(
    context: CurrentTenant,
    db: Db,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[OfferingResource]:
    return CatalogService(db, context).repo.list(OfferingResource, limit, offset)


@router.get("/offering-resources/{identifier}", response_model=CompatibilityProfile)
def compatibility(identifier: UUID, context: CurrentTenant, db: Db) -> OfferingResource:
    return CatalogService(db, context).repo.get(OfferingResource, identifier)


@router.post(
    "/offering-resources", response_model=CompatibilityProfile, status_code=201, dependencies=CSRF
)
def create_compatibility(
    data: CompatibilityWrite, context: CurrentTenant, db: Db
) -> OfferingResource:
    return CatalogService(db, context).compatibility(data)


@router.put(
    "/offering-resources/{identifier}", response_model=CompatibilityProfile, dependencies=CSRF
)
def update_compatibility(
    identifier: UUID, data: CompatibilityWrite, context: CurrentTenant, db: Db
) -> OfferingResource:
    service = CatalogService(db, context)
    service.remove_compatibility(identifier)
    db.flush()
    row = service.compatibility(data)
    row.id = identifier
    db.flush()
    return row


@router.delete("/offering-resources/{identifier}", status_code=204, dependencies=CSRF)
def remove_compatibility(identifier: UUID, context: CurrentTenant, db: Db) -> None:
    CatalogService(db, context).remove_compatibility(identifier)


@router.get("/customers", response_model=list[CustomerProfile])
def customers(
    context: CurrentTenant,
    db: Db,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[Customer]:
    return CatalogService(db, context).repo.list(Customer, limit, offset)


@router.post("/customers", response_model=CustomerProfile, status_code=201, dependencies=CSRF)
def create_customer(data: CustomerWrite, context: CurrentTenant, db: Db) -> Customer:
    return CatalogService(db, context).save_customer(data)


@router.get("/customers/{identifier}", response_model=CustomerProfile)
def customer(identifier: UUID, context: CurrentTenant, db: Db) -> Customer:
    return CatalogService(db, context).repo.get(Customer, identifier)


@router.put("/customers/{identifier}", response_model=CustomerProfile, dependencies=CSRF)
def update_customer(
    identifier: UUID, data: CustomerWrite, context: CurrentTenant, db: Db
) -> Customer:
    return CatalogService(db, context).save_customer(data, identifier)


@router.delete("/customers/{identifier}", status_code=204, dependencies=CSRF)
def delete_customer(identifier: UUID, context: CurrentTenant, db: Db) -> None:
    CatalogService(db, context).delete_customer(identifier)


@router.post("/availability", response_model=AvailabilityProfile, dependencies=CSRF)
def availability(data: BookingInput, context: CurrentTenant, db: Db) -> AvailabilityProfile:
    return AvailabilityService(db, context).check(data)


@router.post("/available-periods", dependencies=CSRF)
def periods(data: PeriodsInput, context: CurrentTenant, db: Db) -> dict[str, object]:
    return available_periods(AvailabilityService(db, context), data)


@router.get("/reservations", response_model=list[ReservationProfile])
def reservations(
    context: CurrentTenant,
    db: Db,
    local_from: date | None = None,
    local_until: date | None = None,
    start_at: AwareDatetime | None = None,
    end_at: AwareDatetime | None = None,
    status: Status | None = None,
    offering_id: UUID | None = None,
    resource_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[ReservationProfile]:
    service = ReservationService(db, context)
    return [
        service.profile(row)
        for row in service.list(
            start_at,
            end_at,
            status,
            offering_id,
            resource_id,
            limit,
            offset,
            local_from,
            local_until,
        )
    ]


@router.post("/reservations", response_model=ReservationProfile, status_code=201, dependencies=CSRF)
def create_reservation(
    data: ReservationWrite, context: CurrentTenant, db: Db
) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.save(data))


@router.get("/reservations/{identifier}", response_model=ReservationProfile)
def reservation(identifier: UUID, context: CurrentTenant, db: Db) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.repo.get(Reservation, identifier))


@router.put("/reservations/{identifier}", response_model=ReservationProfile, dependencies=CSRF)
def update_reservation(
    identifier: UUID, data: ReservationWrite, context: CurrentTenant, db: Db
) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.save(data, identifier))


@router.post(
    "/reservations/{identifier}/reschedule", response_model=ReservationProfile, dependencies=CSRF
)
def reschedule(
    identifier: UUID, data: RescheduleWrite, context: CurrentTenant, db: Db
) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.reschedule(identifier, data))


@router.post(
    "/reservations/{identifier}/resources", response_model=ReservationProfile, dependencies=CSRF
)
def assign(
    identifier: UUID, data: AssignWrite, context: CurrentTenant, db: Db
) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.assign(identifier, data))


@router.post(
    "/reservations/{identifier}/cancel", response_model=ReservationProfile, dependencies=CSRF
)
def cancel(identifier: UUID, context: CurrentTenant, db: Db) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.change_status(identifier, "CANCELLED"))


@router.post(
    "/reservations/{identifier}/status", response_model=ReservationProfile, dependencies=CSRF
)
def change_status(
    identifier: UUID, data: StatusWrite, context: CurrentTenant, db: Db
) -> ReservationProfile:
    service = ReservationService(db, context)
    return service.profile(service.change_status(identifier, data.status, data.expected_version))


@router.delete("/reservations/{identifier}", status_code=204, dependencies=CSRF)
def delete_reservation(identifier: UUID, context: CurrentTenant, db: Db) -> None:
    ReservationService(db, context).remove(identifier)


@router.post("/reservations/{identifier}/retry-sync", dependencies=CSRF)
def retry_sync(identifier: UUID, context: CurrentTenant, db: Db) -> dict[str, str]:
    ReservationService(db, context).retry(identifier)
    return {"status": "PENDING"}


@router.get("/waitlist", response_model=list[WaitlistProfile])
def waitlist(
    context: CurrentTenant,
    db: Db,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[WaitlistEntry]:
    return CatalogService(db, context).repo.list(WaitlistEntry, limit, offset)


@router.post("/waitlist", response_model=WaitlistProfile, status_code=201, dependencies=CSRF)
def create_waitlist(data: WaitlistWrite, context: CurrentTenant, db: Db) -> WaitlistEntry:
    return WaitlistService(db, context).save(data)


@router.get("/waitlist/{identifier}", response_model=WaitlistProfile)
def waitlist_entry(identifier: UUID, context: CurrentTenant, db: Db) -> WaitlistEntry:
    return CatalogService(db, context).repo.get(WaitlistEntry, identifier)


@router.put("/waitlist/{identifier}", response_model=WaitlistProfile, dependencies=CSRF)
def update_waitlist(
    identifier: UUID, data: WaitlistWrite, context: CurrentTenant, db: Db
) -> WaitlistEntry:
    return WaitlistService(db, context).save(data, identifier)


@router.delete("/waitlist/{identifier}", status_code=204, dependencies=CSRF)
def delete_waitlist(identifier: UUID, context: CurrentTenant, db: Db) -> None:
    service = CatalogService(db, context)
    db.delete(service.repo.get(WaitlistEntry, identifier))


@router.get("/synchronizations", response_model=list[OutboxProfile])
def synchronizations(
    context: CurrentTenant,
    db: Db,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[OutboxEvent]:
    return CatalogService(db, context).repo.list(OutboxEvent, limit, offset)
