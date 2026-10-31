import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_auth_tenants import post
from test_booking import booking as booking
from test_booking import offering, reserve
from test_google import GoogleProvider

from app.core.config import get_settings
from app.integrations.google.config import SCOPES, encrypt_token
from app.integrations.google.http import GoogleHTTP
from app.models import BusinessCalendar, GoogleConnection, OutboxEvent, Reservation
from app.services.calendar_sync import claim_work, process_claim

pytestmark = pytest.mark.integration


class CalendarProvider(GoogleProvider):
    def __init__(self):
        super().__init__()
        self.events = {}
        self.writes = []
        self.fail = False
        self.lost_response = False
        self.fail_precondition = False
        self.counter = 0

    def handle(self, request):
        path = request.url.path
        if "/calendars/" not in path or "/events" not in path:
            return super().handle(request)
        self.requests.append(request)
        assert request.headers["authorization"].startswith("Bearer private-")
        calendar, suffix = path.split("/calendars/", 1)[1].split("/events", 1)
        event_id = suffix.lstrip("/")
        key = (calendar, event_id)
        if self.fail:
            return httpx.Response(503, json={"error": "private provider error"})
        if request.method == "GET":
            return (
                httpx.Response(200, json=self.events[key])
                if key in self.events
                else httpx.Response(404, json={"error": "missing"})
            )
        assert request.url.params["sendUpdates"] == "none"
        if request.method == "POST":
            body = json.loads(request.content)
            event_id = body["id"]
            key = (calendar, event_id)
            if key in self.events:
                return httpx.Response(409, json={"error": "exists"})
            assert set(event_id) <= set("0123456789abcdefghijklmnopqrstuv")
            self.counter += 1
            self.events[key] = {**body, "etag": f'"version-{self.counter}"'}
            self.writes.append(("POST", key))
            if self.lost_response:
                self.lost_response = False
                raise httpx.ReadTimeout("accepted response was lost", request=request)
            return httpx.Response(200, json=self.events[key])
        if key not in self.events:
            return httpx.Response(404, json={"error": "missing"})
        if self.fail_precondition or request.headers.get("if-match") != self.events[key]["etag"]:
            return httpx.Response(412, json={"error": "precondition"})
        if request.method == "PATCH":
            self.counter += 1
            self.events[key].update(json.loads(request.content))
            self.events[key]["etag"] = f'"version-{self.counter}"'
            self.writes.append(("PATCH", key))
            return httpx.Response(200, json=self.events[key])
        if request.method == "DELETE":
            self.writes.append(("DELETE", key))
            del self.events[key]
            return httpx.Response(204)
        raise AssertionError("Unexpected calendar method")


@pytest.fixture
def sync_context(booking, database, monkeypatch) -> Iterator[tuple]:
    client, tenant, customer, credentials = booking
    provider = CalendarProvider()
    with monkeypatch.context() as patch:
        patch.setenv("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
        patch.setenv("GOOGLE_CLIENT_SECRET", "test-only-client-secret")
        patch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        get_settings.cache_clear()
        with Session(database[1]) as db, db.begin():
            connection = GoogleConnection(
                tenant_id=UUID(tenant["id"]),
                google_subject="google-subject-one",
                email="test@example.com",
                encrypted_refresh_token=encrypt_token("private-refresh-token"),
                granted_scopes=list(SCOPES),
                connection_status="connected",
            )
            db.add(connection)
            db.flush()
            db.add(
                BusinessCalendar(
                    tenant_id=connection.tenant_id,
                    google_connection_id=connection.id,
                    google_calendar_id="test-calendar@example.com",
                    calendar_name="Test calendar",
                    timezone="Europe/Paris",
                    access_role="owner",
                    is_selected=True,
                )
            )
        url = os.environ.get("TEST_WORKER_DATABASE_URL")
        assert url, "TEST_WORKER_DATABASE_URL is required for outbox tests"
        engine = create_engine(
            make_url(url).set(database=database[1].url.database),
            connect_args={"connect_timeout": 5},
        )
        try:
            yield client, tenant, customer, provider, engine, database[1]
        finally:
            engine.dispose()
            provider.client.close()
            get_settings.cache_clear()


def event_for(owner, reservation_id):
    with Session(owner) as db:
        row = db.scalar(select(Reservation).where(Reservation.id == UUID(reservation_id)))
        return db.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.reservation_id == row.id,
                OutboxEvent.idempotency_key == f"{row.id}:{row.sync_version}",
            )
        )


def run(sync, reservation_id):
    _, _, _, provider, worker, owner = sync
    event_id = event_for(owner, reservation_id)
    claims = claim_work(worker, event_id=event_id)
    assert len(claims) == 1
    process_claim(worker, claims[0], GoogleHTTP(provider.client))
    with Session(owner) as db:
        row = db.get(Reservation, UUID(reservation_id))
        event = db.get(OutboxEvent, event_id)
        return row.calendar_sync_status, event.status, event.last_error


def test_real_rest_adapter_create_modify_cancel_idempotently(sync_context):
    client, tenant, customer, provider, worker, owner = sync_context
    offer = offering(client, default_duration_minutes=60)
    first = reserve(client, customer, offer).json()
    assert run(sync_context, first["id"])[:2] == ("SYNCED", "PROCESSED")
    key = ("test-calendar@example.com", UUID(first["id"]).hex)
    assert provider.events[key]["extendedProperties"]["private"]["reslot_tenant"] == tenant["id"]
    assert len(provider.events) == 1
    assert (
        post(client, "/booking/reservations/" + first["id"] + "/retry-sync", {}).status_code == 200
    )
    assert run(sync_context, first["id"])[0] == "SYNCED"
    assert [method for method, _ in provider.writes] == ["POST"]
    assert (
        post(
            client,
            "/booking/reservations/" + first["id"] + "/reschedule",
            {"start_at": "2027-01-01T12:00Z", "end_at": "2027-01-01T13:00Z"},
        ).status_code
        == 200
    )
    assert run(sync_context, first["id"])[0] == "SYNCED"
    assert [method for method, _ in provider.writes] == ["POST", "PATCH"]
    assert len(provider.events) == 1
    assert post(client, "/booking/reservations/" + first["id"] + "/cancel", {}).status_code == 200
    assert run(sync_context, first["id"])[0] == "SYNCED"
    assert provider.events == {}
    assert (
        post(client, "/booking/reservations/" + first["id"] + "/retry-sync", {}).status_code == 200
    )
    assert run(sync_context, first["id"])[0] == "SYNCED"
    assert [method for method, _ in provider.writes] == ["POST", "PATCH", "DELETE"]


def test_failure_keeps_reservation_and_retries_with_backoff(sync_context):
    client, _, customer, provider, _, owner = sync_context
    first = reserve(client, customer, offering(client)).json()
    provider.fail = True
    assert run(sync_context, first["id"]) == ("FAILED", "FAILED", "google_unavailable")
    assert client.get("/api/v1/booking/reservations/" + first["id"]).json()["status"] == "CONFIRMED"
    with Session(owner) as db:
        event = db.get(OutboxEvent, event_for(owner, first["id"]))
        assert event.attempts == 1 and event.next_attempt_at > datetime.now(UTC)
    provider.fail = False
    assert (
        post(client, "/booking/reservations/" + first["id"] + "/retry-sync", {}).status_code == 200
    )
    assert run(sync_context, first["id"])[0] == "SYNCED"


def test_lost_create_response_and_calendar_change_do_not_duplicate(sync_context):
    client, tenant, customer, provider, _, owner = sync_context
    first = reserve(client, customer, offering(client)).json()
    provider.lost_response = True
    assert run(sync_context, first["id"])[0] == "FAILED"
    assert len(provider.events) == 1
    with Session(owner) as db, db.begin():
        selected = db.scalar(
            select(BusinessCalendar).where(BusinessCalendar.tenant_id == UUID(tenant["id"]))
        )
        selected.google_calendar_id = "different-calendar@example.com"
    assert (
        post(client, "/booking/reservations/" + first["id"] + "/retry-sync", {}).status_code == 200
    )
    assert run(sync_context, first["id"])[0] == "SYNCED"
    assert len(provider.events) == 1
    assert provider.writes == [("POST", ("test-calendar@example.com", UUID(first["id"]).hex))]


def test_external_modifications_and_unrelated_events_are_never_overwritten(sync_context):
    client, _, customer, provider, _, _ = sync_context
    first = reserve(client, customer, offering(client)).json()
    assert run(sync_context, first["id"])[0] == "SYNCED"
    key = next(iter(provider.events))
    provider.events[key]["summary"] = "External change"
    provider.events[key]["etag"] = '"external-change"'
    assert (
        post(
            client,
            "/booking/reservations/" + first["id"] + "/reschedule",
            {"start_at": "2027-01-02T10:00Z", "end_at": "2027-01-02T11:00Z"},
        ).status_code
        == 200
    )
    assert run(sync_context, first["id"]) == ("CONFLICT", "CONFLICT", "calendar_external_conflict")
    assert provider.events[key]["summary"] == "External change"
    assert len(provider.writes) == 1
    assert post(client, "/booking/reservations/" + first["id"] + "/cancel", {}).status_code == 200
    provider.events[key]["extendedProperties"] = {"private": {"reslot_tenant": "another-tenant"}}
    assert run(sync_context, first["id"])[0] == "CONFLICT"
    assert key in provider.events


def test_etag_race_and_worker_lease_recovery(sync_context):
    client, _, customer, provider, worker, owner = sync_context
    first = reserve(client, customer, offering(client)).json()
    eid = event_for(owner, first["id"])
    claim = claim_work(worker, event_id=eid)[0]
    assert claim_work(worker, event_id=eid) == []
    with owner.begin() as db:
        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == eid)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    next_claim = claim_work(worker, event_id=eid)[0]
    process_claim(worker, claim, GoogleHTTP(provider.client))
    assert provider.events == {}
    process_claim(worker, next_claim, GoogleHTTP(provider.client))
    assert len(provider.events) == 1
    assert (
        post(
            client,
            "/booking/reservations/" + first["id"] + "/reschedule",
            {"start_at": "2027-01-02T10:00Z", "end_at": "2027-01-02T11:00Z"},
        ).status_code
        == 200
    )
    provider.fail_precondition = True
    assert run(sync_context, first["id"])[0] == "CONFLICT"
    assert len(provider.writes) == 1


def test_api_database_role_cannot_claim_global_outbox(database):
    for sql in (
        "SELECT * FROM claim_calendar_work(1)",
        "SELECT * FROM claim_calendar_item(gen_random_uuid())",
        "SET ROLE reslot_worker",
        "SET ROLE reslot_outbox_executor",
    ):
        with database[0].connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text(sql))


def test_superseded_versions_and_nightly_calendar_dates(sync_context):
    client, _, customer, provider, worker, owner = sync_context
    first = reserve(client, customer, offering(client, "NIGHTLY")).json()
    old = event_for(owner, first["id"])
    assert (
        post(
            client,
            "/booking/reservations/" + first["id"] + "/reschedule",
            {"local_start_date": "2027-02-01", "local_end_date": "2027-02-04"},
        ).status_code
        == 200
    )
    old_claim = claim_work(worker, event_id=old)[0]
    process_claim(worker, old_claim, GoogleHTTP(provider.client))
    assert provider.events == {}
    assert run(sync_context, first["id"])[0] == "SYNCED"
    event = next(iter(provider.events.values()))
    assert event["start"] == {"date": "2027-02-01"}
    assert event["end"] == {"date": "2027-02-04"}


def test_lost_insert_followed_by_multiple_edits_recovers_original_snapshot(sync_context):
    client, _, customer, provider, _, _ = sync_context
    first = reserve(client, customer, offering(client)).json()
    provider.lost_response = True
    assert run(sync_context, first["id"])[0] == "FAILED"
    for hour in (12, 14):
        response = post(
            client,
            "/booking/reservations/" + first["id"] + "/reschedule",
            {"start_at": f"2027-01-01T{hour:02}:00Z", "end_at": f"2027-01-01T{hour + 1:02}:00Z"},
        )
        assert response.status_code == 200, response.text
    assert run(sync_context, first["id"])[0] == "SYNCED"
    assert len(provider.events) == 1
    assert [method for method, _ in provider.writes] == ["POST", "PATCH"]
    remote = next(iter(provider.events.values()))
    assert remote["extendedProperties"]["private"]["reslot_version"] == "3"
