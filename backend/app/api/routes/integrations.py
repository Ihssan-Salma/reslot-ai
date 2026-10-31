from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import Db
from app.api.routes.google import GoogleClient, Manager, safe_response
from app.core.security import require_csrf
from app.schemas.automation import (
    CalendarAvailability,
    CalendarAvailabilityInput,
    CustomerRecord,
    EventNormalization,
    SpreadsheetInput,
    SpreadsheetStatus,
    WaitingListRecord,
    WaitlistStatusInput,
)
from app.services.calendar_reservations import CalendarReservationService
from app.services.google_sheets import GoogleSheetsService

router = APIRouter(prefix="/api/v1/integrations", tags=["automation integrations"])


@router.get("/sheets", response_model=SpreadsheetStatus)
def status(db: Db, context: Manager, http: GoogleClient) -> SpreadsheetStatus | JSONResponse:
    return safe_response(lambda: GoogleSheetsService(db, context, http).status())


@router.put("/sheets", response_model=SpreadsheetStatus, dependencies=[Depends(require_csrf)])
def configure(
    data: SpreadsheetInput, db: Db, context: Manager, http: GoogleClient
) -> SpreadsheetStatus | JSONResponse:
    return safe_response(
        lambda: GoogleSheetsService(db, context, http).configure(data.spreadsheet_id)
    )


@router.delete("/sheets", dependencies=[Depends(require_csrf)])
def clear(db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(lambda: GoogleSheetsService(db, context, http).clear())


@router.get("/sheets/health")
def health(db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(lambda: GoogleSheetsService(db, context, http).health())


@router.get("/sheets/customers", response_model=list[CustomerRecord])
def customers(db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(lambda: GoogleSheetsService(db, context, http).get_customers())


@router.get("/sheets/customers/{customer_id}", response_model=CustomerRecord)
def customer(customer_id: str, db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(lambda: GoogleSheetsService(db, context, http).get_customer(customer_id))


@router.get("/sheets/waiting-list", response_model=list[WaitingListRecord])
def waiting_list(db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(lambda: GoogleSheetsService(db, context, http).get_waiting_list())


@router.get("/sheets/waiting-list/{entry_id}", response_model=WaitingListRecord)
def entry(entry_id: str, db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(
        lambda: GoogleSheetsService(db, context, http).get_waitlist_entry(entry_id)
    )


@router.post("/sheets/waiting-list/{entry_id}/status", dependencies=[Depends(require_csrf)])
def update_status(
    entry_id: str, data: WaitlistStatusInput, db: Db, context: Manager, http: GoogleClient
) -> Any:
    return safe_response(
        lambda: GoogleSheetsService(db, context, http).update_waitlist_status(
            entry_id, data.status, data.idempotency_key
        )
    )


@router.get("/calendar/reservations/{event_id}", response_model=EventNormalization)
def reservation_event(event_id: str, db: Db, context: Manager, http: GoogleClient) -> Any:
    return safe_response(
        lambda: CalendarReservationService(db, context, http).get_reservation_event(event_id)
    )


@router.post(
    "/calendar/availability",
    response_model=CalendarAvailability,
    dependencies=[Depends(require_csrf)],
)
def availability(
    data: CalendarAvailabilityInput, db: Db, context: Manager, http: GoogleClient
) -> Any:
    return safe_response(
        lambda: CalendarReservationService(db, context, http).check_calendar_availability(data)
    )
