from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.domain import BookingError
from app.models import BookingPolicy, Offering
from app.schemas.booking import WindowInput


@dataclass(frozen=True)
class BookingWindow:
    start: datetime
    end: datetime
    local_start: date | None = None
    local_end: date | None = None


def local_instant(value: datetime, timezone: str) -> datetime:
    if value.tzinfo is not None:
        raise BookingError(
            "Local wall times must not include an offset; use start_at/end_at for explicit offsets",
            422,
        )
    zone = ZoneInfo(timezone)
    variants = [value.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [
        item
        for item in variants
        if item.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value
    ]
    if not valid:
        raise BookingError("This local time does not exist because of daylight saving time", 422)
    if len({item.utcoffset() for item in valid}) > 1:
        raise BookingError(
            "This local time is ambiguous; supply an explicit UTC offset using start_at/end_at", 422
        )
    return valid[0].astimezone(UTC)


class TimedPolicy:
    def resolve(
        self, data: WindowInput, offering: Offering, policy: BookingPolicy, timezone: str
    ) -> BookingWindow:
        if data.local_start_date or data.local_end_date:
            raise BookingError("Local dates are only valid for nightly bookings", 422)
        if (data.start_at and data.local_start_at) or (data.end_at and data.local_end_at):
            raise BookingError("Choose explicit offsets or local wall times, not both", 422)
        start = data.start_at or (
            local_instant(data.local_start_at, timezone) if data.local_start_at else None
        )
        end = data.end_at or (
            local_instant(data.local_end_at, timezone) if data.local_end_at else None
        )
        if start and end is None and offering.default_duration_minutes:
            end = start + timedelta(minutes=offering.default_duration_minutes)
        if start is None or end is None or end <= start:
            raise BookingError("Provide a start and later end time", 422)
        minutes = (end - start).total_seconds() / 60
        if offering.min_duration_minutes and minutes < offering.min_duration_minutes:
            raise BookingError("Reservation is shorter than the minimum duration", 422)
        if offering.max_duration_minutes and minutes > offering.max_duration_minutes:
            raise BookingError("Reservation exceeds the maximum duration", 422)
        if (
            offering.default_duration_minutes
            and not offering.min_duration_minutes
            and not offering.max_duration_minutes
            and minutes != offering.default_duration_minutes
        ):
            raise BookingError("Reservation must use the offering's fixed duration", 422)
        return BookingWindow(start.astimezone(UTC), end.astimezone(UTC))


class NightlyPolicy:
    def resolve(
        self, data: WindowInput, offering: Offering, policy: BookingPolicy, timezone: str
    ) -> BookingWindow:
        first, last = data.local_start_date, data.local_end_date
        if first is None or last is None or last <= first:
            raise BookingError("Provide check-in and later check-out dates", 422)
        if data.start_at or data.end_at or data.local_start_at or data.local_end_at:
            raise BookingError("Nightly bookings use local dates, not explicit timestamps", 422)
        nights = (last - first).days
        if nights < policy.min_nights or (policy.max_nights and nights > policy.max_nights):
            raise BookingError("Stay length violates the offering's nightly policy", 422)
        return BookingWindow(
            local_instant(datetime.combine(first, policy.check_in_time), timezone),
            local_instant(datetime.combine(last, policy.check_out_time), timezone),
            first,
            last,
        )


class WindowPolicy(Protocol):
    def resolve(
        self, data: WindowInput, offering: Offering, policy: BookingPolicy, timezone: str
    ) -> BookingWindow: ...


POLICIES: dict[str, WindowPolicy] = {
    "TIMED": TimedPolicy(),
    "CAPACITY": TimedPolicy(),
    "NIGHTLY": NightlyPolicy(),
}


def resolve_window(
    data: WindowInput, offering: Offering, policy: BookingPolicy, timezone: str
) -> BookingWindow:
    return POLICIES[offering.booking_mode].resolve(data, offering, policy, timezone)


def peak_usage(
    intervals: list[tuple[datetime, datetime, int]], start: datetime, end: datetime
) -> int:
    changes: dict[datetime, int] = {}
    for first, last, quantity in intervals:
        if first < end and last > start:
            a, b = max(start, first), min(end, last)
            changes[a] = changes.get(a, 0) + quantity
            changes[b] = changes.get(b, 0) - quantity
    current = peak = 0
    for instant in sorted(changes):
        current += changes[instant]
        peak = max(peak, current)
    return peak
