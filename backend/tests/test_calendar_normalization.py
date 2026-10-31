import json

import pytest

from app.services.calendar_reservations import normalize_reservation_event


def event():
    return {
        "id": "external-1",
        "start": {"dateTime": "2027-01-01T10:00:00+01:00"},
        "end": {"dateTime": "2027-01-01T11:00:00+01:00"},
        "extendedProperties": {
            "private": {
                "reslot_kind": "reservation",
                "reslot_schema": "1",
                "reslot_reference": "REF-1",
                "reslot_offering": "Dinner",
                "reslot_quantity": "1",
            }
        },
    }


@pytest.mark.parametrize(
    "offering", ["Dinner for two", "Overnight stay", "Haircut", "Consultation", "Desk pass"]
)
def test_generic_reservation_types(offering):
    data = event()
    data["extendedProperties"]["private"]["reslot_offering"] = offering
    result = normalize_reservation_event(data, "business-calendar", "Europe/Paris")
    assert result.classification == "reservation"
    assert result.reservation.offering == offering
    assert result.reservation.start_at.isoformat() == "2027-01-01T09:00:00+00:00"


def test_manual_description_and_all_day_timezone():
    data = event()
    data.pop("extendedProperties")
    data["description"] = (
        "Notes\n[ReSlot Reservation v1]\n"
        + json.dumps(
            {"reservation_reference": "HOTEL-1", "offering": "Overnight stay", "quantity": 2}
        )
        + "\n[/ReSlot Reservation]"
    )
    data["start"] = {"date": "2027-03-28"}
    data["end"] = {"date": "2027-03-29"}
    result = normalize_reservation_event(data, "calendar", "Europe/Paris")
    assert result.classification == "reservation"
    assert result.reservation.all_day is True
    assert (result.reservation.end_at - result.reservation.start_at).total_seconds() == 23 * 3600


@pytest.mark.parametrize(
    "problem",
    [
        "incomplete",
        "legacy",
        "conflict",
        "duplicate_json",
        "bad_dates",
        "dst_gap",
        "dst_ambiguous",
        "wrong_tenant",
        "bad_marker",
    ],
)
def test_ambiguous_events_require_review(problem):
    data = event()
    if problem == "incomplete":
        data["extendedProperties"]["private"].pop("reslot_quantity")
    if problem == "legacy":
        data["extendedProperties"] = {"private": {"reslot_reservation": "legacy-reference"}}
    if problem == "conflict":
        data["description"] = (
            '[ReSlot Reservation v1]{"reservation_reference":"OTHER",'
            '"offering":"Dinner","quantity":1}[/ReSlot Reservation]'
        )
    if problem == "duplicate_json":
        data.pop("extendedProperties")
        data["description"] = (
            '[ReSlot Reservation v1]{"reservation_reference":"ONE",'
            '"reservation_reference":"TWO","offering":"Dinner","quantity":1}'
            "[/ReSlot Reservation]"
        )
    if problem == "bad_dates":
        data["end"] = data["start"]
    if problem == "dst_gap":
        data["start"] = {"dateTime": "2027-03-28T02:30:00", "timeZone": "Europe/Paris"}
    if problem == "dst_ambiguous":
        data["start"] = {"dateTime": "2027-10-31T02:30:00", "timeZone": "Europe/Paris"}
    if problem == "wrong_tenant":
        data["extendedProperties"]["private"]["reslot_tenant_id"] = "another-tenant"
    if problem == "bad_marker":
        data["description"] = "[ReSlot Reservation v2]invalid"
    result = normalize_reservation_event(data, "calendar", "Europe/Paris")
    assert result.classification == "review"
    assert result.reservation is None


def test_unmarked_calendar_event_is_ignored():
    data = event()
    data.pop("extendedProperties")
    data["summary"] = "Restaurant reservation"
    assert normalize_reservation_event(data, "calendar", "UTC").classification == "ignored"
