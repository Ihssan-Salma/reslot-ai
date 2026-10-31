import json
import re
from datetime import UTC, date, datetime, time
from typing import Any
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP
from app.models.automation import CalendarEventSnapshot
from app.models.google import BusinessCalendar
from app.repositories.automation import AutomationRepository
from app.schemas.automation import (
    CalendarAvailability,
    CalendarAvailabilityInput,
    EventNormalization,
    NormalizedReservation,
    ReservationMetadata,
)
from app.services.google import CALENDAR_URL, GoogleService

OPEN_MARKER = "[ReSlot Reservation v1]"
CLOSE_MARKER = "[/ReSlot Reservation]"
PRIVATE_FIELDS = {
    "reslot_reference": "reservation_reference",
    "reslot_offering": "offering",
    "reslot_resource": "resource_label",
    "reslot_party_size": "party_size",
    "reslot_quantity": "quantity",
    "reslot_requirements": "additional_requirements",
    "reslot_customer_reference": "customer_reference",
}


def local_time(value: datetime, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    options = {
        candidate.astimezone(UTC)
        for fold in (0, 1)
        if (candidate := value.replace(tzinfo=zone, fold=fold))
        .astimezone(UTC)
        .astimezone(zone)
        .replace(tzinfo=None)
        == value
    }
    if len(options) != 1:
        raise ValueError("Ambiguous or nonexistent local time")
    return options.pop()


def event_window(event: dict[str, Any], timezone: str) -> tuple[datetime, datetime, bool]:
    def endpoint(part: Any) -> tuple[datetime, bool]:
        if not isinstance(part, dict):
            raise ValueError("Missing event time")
        if part.get("dateTime") and part.get("date"):
            raise ValueError("Mixed event time formats")
        if part.get("dateTime"):
            parsed = datetime.fromisoformat(str(part["dateTime"]).replace("Z", "+00:00"))
            return (
                parsed.astimezone(UTC)
                if parsed.tzinfo
                else local_time(parsed, str(part.get("timeZone", timezone)))
            ), False
        value = str(part.get("date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Missing date or date-time")
        return local_time(datetime.combine(date.fromisoformat(value), time()), timezone), True

    start, all_day = endpoint(event.get("start"))
    end, end_all_day = endpoint(event.get("end"))
    if all_day != end_all_day or end <= start:
        raise ValueError("Invalid event interval")
    return start, end, all_day


def normalize_reservation_event(
    event: dict[str, Any], calendar_id: str, timezone: str, tenant_id: UUID | None = None
) -> EventNormalization:
    result = EventNormalization(
        calendar_id=calendar_id,
        event_id=str(event.get("id", "")),
        classification="ignored",
        source_status=str(event.get("status", "confirmed")),
    )
    properties = event.get("extendedProperties") or {}
    private = properties.get("private", {}) if isinstance(properties, dict) else {}
    if not isinstance(private, dict):
        result.classification = "review"
        result.review_reasons = ["invalid_or_ambiguous_reservation_metadata"]
        return result
    description = str(event.get("description", ""))
    marked_private = any(str(key).startswith("reslot_") for key in private)
    marked_description = "[ReSlot Reservation" in description or CLOSE_MARKER in description
    if not marked_private and not marked_description:
        return result
    try:
        metadata: ReservationMetadata | None = None
        if marked_private:
            if private.get("reslot_kind") != "reservation" or private.get("reslot_schema") != "1":
                raise ValueError("Unsupported private metadata")
            if private.get("reslot_tenant_id") and str(tenant_id) != private["reslot_tenant_id"]:
                raise ValueError("Tenant metadata mismatch")
            metadata = ReservationMetadata.model_validate(
                {target: private[key] for key, target in PRIVATE_FIELDS.items() if key in private}
            )
        if marked_description:
            if description.count(OPEN_MARKER) != 1 or description.count(CLOSE_MARKER) != 1:
                raise ValueError("Invalid description markers")
            start_index = description.index(OPEN_MARKER) + len(OPEN_MARKER)
            end_index = description.index(CLOSE_MARKER)
            if end_index <= start_index or end_index - start_index > 10000:
                raise ValueError("Invalid description block")

            def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                obj: dict[str, Any] = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("Duplicate metadata field")
                    obj[key] = value
                return obj

            manual = ReservationMetadata.model_validate(
                json.loads(description[start_index:end_index], object_pairs_hook=unique_object)
            )
            if metadata and metadata != manual:
                raise ValueError("Conflicting metadata sources")
            metadata = manual
        if metadata is None or not result.event_id:
            raise ValueError("Incomplete reservation")
        start, end, all_day = event_window(event, timezone)
        result.reservation = NormalizedReservation(
            **metadata.model_dump(),
            calendar_id=calendar_id,
            event_id=result.event_id,
            start_at=start,
            end_at=end,
            all_day=all_day,
        )
        result.classification = "reservation"
    except (ValueError, TypeError, KeyError, ValidationError):
        result.classification = "review"
        result.review_reasons = ["invalid_or_ambiguous_reservation_metadata"]
    return result


class CalendarReservationService:
    def __init__(self, db: Session, context: TenantContext, http: GoogleHTTP):
        self.db = db
        self.google = GoogleService(db, context, http)
        self.repository = AutomationRepository(db, context.tenant.id)

    def selected_calendar(self) -> BusinessCalendar:
        self.google.connected()
        calendar = next(
            (row for row in self.google.repository.calendars() if row.is_selected), None
        )
        if not calendar:
            raise GoogleError("calendar_not_selected", "Select a business calendar first", 409)
        self.google.check_calendar(calendar)
        return calendar

    def get_reservation_event(self, event_id: str) -> EventNormalization:
        calendar = self.selected_calendar()
        if not event_id or len(event_id) > 1024:
            raise GoogleError("invalid_event_id", "Invalid event ID", 422)
        event = self.google.get(
            CALENDAR_URL
            + "/calendars/"
            + quote(calendar.google_calendar_id, safe="")
            + "/events/"
            + quote(event_id, safe="")
        )
        if event.get("id") != event_id:
            raise GoogleError("invalid_google_response", "Google returned an unexpected event")
        result = normalize_reservation_event(
            event, calendar.google_calendar_id, calendar.timezone, self.google.tenant.id
        )
        subject = self.google.connected().google_subject
        snapshot = self.repository.snapshot(calendar.google_calendar_id, event_id)
        if snapshot is None:
            snapshot = CalendarEventSnapshot(
                tenant_id=self.google.tenant.id,
                calendar_id=calendar.google_calendar_id,
                event_id=event_id,
            )
            self.db.add(snapshot)
        snapshot.google_subject = subject
        snapshot.etag = event.get("etag")
        snapshot.classification, snapshot.source_status = (
            result.classification,
            result.source_status,
        )
        snapshot.normalized_payload = (
            result.reservation.model_dump(mode="json") if result.reservation else None
        )
        snapshot.review_reasons = result.review_reasons
        self.db.flush()
        return result

    def check_calendar_availability(
        self, window: CalendarAvailabilityInput
    ) -> CalendarAvailability:
        calendar = self.selected_calendar()
        if window.exclude_event_id:
            excluded = self.get_reservation_event(window.exclude_event_id)
            if excluded.classification != "reservation":
                raise GoogleError(
                    "unsafe_event_exclusion", "Only a validated reservation can be excluded", 409
                )
        result = CalendarAvailability(calendar_id=calendar.google_calendar_id, available=True)
        page = ""
        for _ in range(100):
            payload = self.google.get(
                CALENDAR_URL
                + "/calendars/"
                + quote(calendar.google_calendar_id, safe="")
                + "/events",
                {
                    "timeMin": window.start_at.isoformat(),
                    "timeMax": window.end_at.isoformat(),
                    "singleEvents": "true",
                    "showDeleted": "false",
                    "maxResults": 2500,
                    "pageToken": page,
                    "fields": "nextPageToken,items(id,status,transparency,start,end)",
                },
            )
            for event in payload.get("items", []):
                if (
                    event.get("status") == "cancelled"
                    or event.get("transparency") == "transparent"
                    or event.get("id") == window.exclude_event_id
                ):
                    continue
                try:
                    start, end, _ = event_window(event, calendar.timezone)
                    if start < window.end_at and end > window.start_at:
                        if not event.get("id"):
                            raise ValueError("Missing busy event ID")
                        result.conflicting_event_ids.append(str(event["id"]))
                except (ValueError, TypeError, KeyError):
                    result.requires_review = True
                    result.review_reasons = ["invalid_busy_event_interval"]
            page = str(payload.get("nextPageToken", ""))
            if not page:
                break
        else:
            raise GoogleError(
                "calendar_limit_exceeded", "Availability results exceed the safe page limit", 409
            )
        result.available = not result.requires_review and not result.conflicting_event_ids
        return result
