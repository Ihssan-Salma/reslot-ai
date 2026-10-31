from datetime import timedelta
from zoneinfo import ZoneInfo

from app.core.domain import BookingError
from app.models import Offering
from app.schemas.booking import BookingInput, PeriodsInput
from app.services.availability import AvailabilityService


def available_periods(service: AvailabilityService, data: PeriodsInput) -> dict[str, object]:
    if data.end_at <= data.start_at or data.end_at - data.start_at > timedelta(days=31):
        raise BookingError("Search a positive date range of at most 31 days", 422)
    offering = service.repo.get(Offering, data.offering_id)
    periods = []
    cursor = data.start_at
    count = 0
    zone = ZoneInfo(service.context.tenant.timezone)
    while cursor < data.end_at and count < 200:
        values = {
            "offering_id": data.offering_id,
            "party_size": data.party_size,
            "quantity": data.quantity,
        }
        if offering.booking_mode == "NIGHTLY":
            first = cursor.astimezone(zone).date()
            request = BookingInput(
                **values, local_start_date=first, local_end_date=first + timedelta(days=data.nights)
            )
            from datetime import datetime

            from app.services.booking_policies import local_instant

            cursor = local_instant(
                datetime.combine(first + timedelta(days=1), offering.policy.check_in_time),
                service.context.tenant.timezone,
            )
        else:
            minutes = (
                data.duration_minutes
                or offering.default_duration_minutes
                or offering.min_duration_minutes
            )
            if minutes is None:
                raise BookingError("Specify a duration for this flexible offering", 422)
            request = BookingInput(
                **values, start_at=cursor, end_at=cursor + timedelta(minutes=minutes)
            )
            cursor += timedelta(minutes=data.step_minutes)
        result = service.check(request)
        if (
            result.available
            and result.start_at is not None
            and result.end_at is not None
            and result.start_at >= data.start_at
            and result.end_at <= data.end_at
        ):
            periods.append(result.model_dump(mode="json"))
        count += 1
    return {
        "periods": periods,
        "truncated": cursor < data.end_at,
        "next_start_at": cursor.isoformat() if cursor < data.end_at else None,
    }
