from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.integrations.google.http import GoogleHTTP
from app.mcp.schemas import Availability, AvailabilityInput, EventInput, ToolResult
from app.schemas.automation import CalendarAvailabilityInput
from app.services.calendar_reservations import CalendarReservationService


def get_event(
    db: Session, context: TenantContext, http: GoogleHTTP, args: EventInput
) -> ToolResult:
    result = CalendarReservationService(db, context, http).get_reservation_event(args.event_id)
    return ToolResult(
        category="OK" if result.classification == "reservation" else "REVIEW_REQUIRED",
        message="Reservation metadata"
        if result.classification == "reservation"
        else "Event requires review before automation",
        data=result,
    )


def availability(
    db: Session,
    context: TenantContext,
    http: GoogleHTTP,
    args: AvailabilityInput,
) -> ToolResult:
    result = CalendarReservationService(db, context, http).check_calendar_availability(
        CalendarAvailabilityInput(start_at=args.start_at, end_at=args.end_at)
    )
    review = result.requires_review or args.resource_label is not None
    data = Availability(
        calendar_available=result.available,
        conflicting_event_ids=result.conflicting_event_ids,
        requires_review=review,
        review_reasons=result.review_reasons
        + (["resource_capacity_not_proven_by_calendar"] if args.resource_label else []),
    )
    return ToolResult(
        category="REVIEW_REQUIRED" if review else "OK",
        message="Calendar conflicts are not proof of resource capacity",
        data=data,
    )
