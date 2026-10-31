from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_auth_tenants import post, register_login

from app.db.session import set_tenant_context, set_user_context
from app.main import create_app
from app.models import (
    Customer,
    Offering,
    OutboxEvent,
    Reservation,
    ReservationResource,
    Resource,
    TenantMembership,
    WaitlistEntry,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def booking(client):
    credentials = register_login(client)
    tenant = post(
        client,
        "/tenants",
        {"name": "Reservation Business", "business_type": "other", "timezone": "Europe/Paris"},
    ).json()
    response = post(
        client,
        "/booking/customers",
        {
            "full_name": "Test Customer",
            "email": uuid4().hex + "@example.com",
            "notification_consent": True,
        },
    )
    assert response.status_code == 201, response.text
    return client, tenant, response.json(), credentials


def offering(client, mode="TIMED", **values):
    body = {"name": "Reservable offering", "category": "general", "booking_mode": mode, **values}
    response = post(client, "/booking/offerings", body)
    assert response.status_code == 201, response.text
    return response.json()


def resource(client, offer, name="Resource", kind="asset", **values):
    response = post(client, "/booking/resources", {"name": name, "resource_type": kind, **values})
    assert response.status_code == 201, response.text
    row = response.json()
    response = post(
        client,
        "/booking/offering-resources",
        {"offering_id": offer["id"], "resource_id": row["id"]},
    )
    assert response.status_code == 201, response.text
    return row


def reserve(client, customer, offer, **values):
    body = {
        "customer_id": customer["id"],
        "offering_id": offer["id"],
        **(
            {"start_at": "2027-01-01T10:00:00+00:00", "end_at": "2027-01-01T11:00:00+00:00"}
            if offer["booking_mode"] != "NIGHTLY"
            else {"local_start_date": "2027-01-01", "local_end_date": "2027-01-04"}
        ),
        **values,
    }
    return post(client, "/booking/reservations", body)


def test_restaurant_capacity_compatibility_and_half_open_intervals(booking):
    client, _, customer, _ = booking
    offer = offering(
        client,
        default_duration_minutes=60,
        policy={
            "require_resources": True,
            "requirements": [{"resource_type": "table", "required_count": 1}],
        },
    )
    tables = [
        resource(client, offer, f"Table {capacity}", "table", capacity=capacity)
        for capacity in (2, 4, 6)
    ]
    first = reserve(client, customer, offer, party_size=5)
    assert first.status_code == 201, first.text
    assert first.json()["resource_ids"] == [tables[2]["id"]]
    assert reserve(client, customer, offer, party_size=5).status_code == 409
    assert (
        reserve(client, customer, offer, party_size=2, resource_ids=[tables[0]["id"]]).status_code
        == 201
    )
    assert (
        reserve(client, customer, offer, party_size=2, resource_ids=[tables[0]["id"]]).status_code
        == 409
    )
    adjacent = reserve(
        client,
        customer,
        offer,
        party_size=2,
        resource_ids=[tables[0]["id"]],
        start_at="2027-01-01T11:00:00Z",
        end_at="2027-01-01T12:00:00Z",
    )
    assert adjacent.status_code == 201, adjacent.text
    assert reserve(client, customer, offer, party_size=7).status_code == 409


def test_hotel_stays_and_checkout_policy(booking):
    client, _, customer, _ = booking
    offer = offering(
        client,
        "NIGHTLY",
        policy={
            "require_resources": True,
            "check_in_time": "15:00",
            "check_out_time": "11:00",
            "min_nights": 1,
        },
    )
    room = resource(client, offer, "Room 101", "room", capacity=2)
    resource(client, offer, "Room 102", "room", capacity=3)
    first = reserve(client, customer, offer, party_size=2, resource_ids=[room["id"]])
    assert first.status_code == 201, first.text
    assert first.json()["local_start_date"] == "2027-01-01"
    assert first.json()["start_at"].startswith("2027-01-01T14:00")
    assert (
        reserve(
            client,
            customer,
            offer,
            resource_ids=[room["id"]],
            local_start_date="2027-01-03",
            local_end_date="2027-01-05",
        ).status_code
        == 409
    )
    assert (
        reserve(
            client,
            customer,
            offer,
            resource_ids=[room["id"]],
            local_start_date="2027-01-04",
            local_end_date="2027-01-06",
        ).status_code
        == 201
    )
    assert (
        reserve(
            client,
            customer,
            offer,
            party_size=3,
            resource_ids=[room["id"]],
            local_start_date="2027-02-01",
            local_end_date="2027-02-02",
        ).status_code
        == 409
    )


def test_multiple_required_resource_types(booking):
    client, _, customer, _ = booking
    offer = offering(
        client,
        default_duration_minutes=60,
        policy={
            "requirements": [
                {"resource_type": "professional", "required_count": 1},
                {"resource_type": "room", "required_count": 1},
            ]
        },
    )
    employee = resource(client, offer, "Professional Sarah", "professional")
    room = resource(client, offer, "Treatment Room", "room")
    assert reserve(client, customer, offer, resource_ids=[employee["id"]]).status_code == 409
    first = reserve(client, customer, offer)
    assert first.status_code == 201, first.text
    assert set(first.json()["resource_ids"]) == {employee["id"], room["id"]}
    assert reserve(client, customer, offer).status_code == 409
    other = offering(client, name="Other appointment", default_duration_minutes=60)
    assert (
        post(
            client,
            "/booking/offering-resources",
            {"offering_id": other["id"], "resource_id": employee["id"]},
        ).status_code
        == 201
    )
    assert reserve(client, customer, other).status_code == 409


def test_shared_capacity_counts_peak_usage_not_sum_of_disjoint_intervals(booking):
    client, _, customer, _ = booking
    offer = offering(client, "CAPACITY", capacity_limit=4)
    shared = resource(client, offer, "Shared desks", "desk", capacity=3, allocation_mode="SHARED")
    assert (
        reserve(
            client,
            customer,
            offer,
            quantity=2,
            start_at="2027-01-01T10:00Z",
            end_at="2027-01-01T11:00Z",
        ).status_code
        == 201
    )
    assert (
        reserve(
            client,
            customer,
            offer,
            quantity=2,
            start_at="2027-01-01T11:00Z",
            end_at="2027-01-01T12:00Z",
        ).status_code
        == 201
    )
    assert (
        reserve(
            client,
            customer,
            offer,
            quantity=1,
            start_at="2027-01-01T10:00Z",
            end_at="2027-01-01T12:00Z",
        ).status_code
        == 201
    )
    assert (
        reserve(
            client,
            customer,
            offer,
            quantity=1,
            start_at="2027-01-01T10:30Z",
            end_at="2027-01-01T11:30Z",
        ).status_code
        == 409
    )
    assert shared["capacity"] == 3


@pytest.mark.parametrize("exclusive", [True, False])
def test_simultaneous_requests_cannot_overbook(booking, exclusive):
    client, tenant, customer, credentials = booking
    offer = offering(
        client,
        "TIMED" if exclusive else "CAPACITY",
        **({"default_duration_minutes": 60} if exclusive else {"capacity_limit": 3}),
    )
    if exclusive:
        resource(client, offer)
    barrier = Barrier(2)
    with TestClient(create_app()) as a, TestClient(create_app()) as b:
        for other in (a, b):
            assert post(other, "/auth/login", credentials).status_code == 200
            assert post(other, "/tenants/switch", {"tenant_id": tenant["id"]}).status_code == 200

        def attempt(other):
            token = other.get("/api/v1/auth/csrf").json()["csrf_token"]
            barrier.wait(timeout=10)
            return other.post(
                "/api/v1/booking/reservations",
                json={
                    "customer_id": customer["id"],
                    "offering_id": offer["id"],
                    "start_at": "2027-01-01T10:00:00Z",
                    "end_at": "2027-01-01T11:00:00Z",
                    "quantity": 1 if exclusive else 2,
                },
                headers={"Origin": "http://localhost:5173", "X-CSRF-Token": token},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (a, b)))
    assert sorted(results) == [201, 409]


def test_cancel_releases_capacity_and_outbox_is_atomic(booking, database):
    client, _, customer, _ = booking
    offer = offering(client, default_duration_minutes=60)
    resource(client, offer)
    first = reserve(client, customer, offer)
    assert first.status_code == 201, first.text
    rid = first.json()["id"]
    assert post(client, "/booking/reservations/" + rid + "/cancel", {}).status_code == 200
    assert reserve(client, customer, offer).status_code == 201
    with Session(database[1]) as db:
        events = db.scalars(
            select(OutboxEvent).where(OutboxEvent.reservation_id == UUID(rid))
        ).all()
        assert {event.event_type for event in events} == {"calendar.upsert", "calendar.delete"}
        allocations = db.scalars(
            select(ReservationResource).where(ReservationResource.reservation_id == UUID(rid))
        ).all()
        assert all(row.allocation_status == "RELEASED" for row in allocations)
    before = client.get("/api/v1/booking/synchronizations").json()
    assert reserve(client, customer, offer).status_code == 409
    assert len(client.get("/api/v1/booking/synchronizations").json()) == len(before)


def test_rescheduling_and_stale_version_protection(booking):
    client, _, customer, _ = booking
    offer = offering(client, default_duration_minutes=60)
    resource(client, offer)
    first = reserve(client, customer, offer).json()
    response = post(
        client,
        "/booking/reservations/" + first["id"] + "/reschedule",
        {"start_at": "2027-01-01T12:00Z", "end_at": "2027-01-01T13:00Z", "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sync_version"] == 2
    assert (
        post(
            client,
            "/booking/reservations/" + first["id"] + "/reschedule",
            {"start_at": "2027-01-01T14:00Z", "end_at": "2027-01-01T15:00Z", "expected_version": 1},
        ).status_code
        == 409
    )
    assert reserve(client, customer, offer).status_code == 201


def test_waitlist_supports_timed_and_nightly_without_booking(booking):
    client, _, customer, _ = booking
    for mode in ("TIMED", "NIGHTLY"):
        offer = offering(client, mode)
        body = {"customer_id": customer["id"], "offering_id": offer["id"], "quantity": 1}
        body.update(
            {"local_start_date": "2027-01-01", "local_end_date": "2027-01-04"}
            if mode == "NIGHTLY"
            else {"available_from": "2027-01-01T10:00Z", "available_until": "2027-01-03T18:00Z"}
        )
        response = post(client, "/booking/waitlist", body)
        assert response.status_code == 201, response.text
        mixed = {
            **body,
            "available_from": "2027-01-01T10:00Z",
            "available_until": "2027-01-03T18:00Z",
            "local_start_date": "2027-01-01",
            "local_end_date": "2027-01-03",
        }
        assert post(client, "/booking/waitlist", mixed).status_code == 422
        assert client.get("/api/v1/booking/reservations").json() == []
        assert (
            client.delete(
                "/api/v1/booking/waitlist/" + response.json()["id"],
                headers={
                    "Origin": "http://localhost:5173",
                    "X-CSRF-Token": client.get("/api/v1/auth/csrf").json()["csrf_token"],
                },
            ).status_code
            == 204
        )


def test_nightly_and_timed_dst_validation(booking):
    client, _, customer, _ = booking
    nightly = offering(client, "NIGHTLY")
    response = reserve(
        client, customer, nightly, local_start_date="2027-03-27", local_end_date="2027-03-29"
    )
    assert response.status_code == 201, response.text
    row = response.json()
    elapsed = datetime.fromisoformat(row["end_at"]) - datetime.fromisoformat(row["start_at"])
    assert elapsed == timedelta(hours=43)
    timed = offering(client)
    for first, last in (
        ("2027-03-28T02:30", "2027-03-28T04:00"),
        ("2027-10-31T02:30", "2027-10-31T04:00"),
    ):
        assert (
            post(
                client,
                "/booking/reservations",
                {
                    "customer_id": customer["id"],
                    "offering_id": timed["id"],
                    "local_start_at": first,
                    "local_end_at": last,
                },
            ).status_code
            == 422
        )
    assert (
        reserve(
            client,
            customer,
            timed,
            start_at="2027-10-31T02:30:00+02:00",
            end_at="2027-10-31T02:30:00+01:00",
        ).status_code
        == 201
    )


def test_other_timezone_and_available_periods(booking):
    client, _, customer, _ = booking
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = client.put(
        "/api/v1/booking/business",
        json={"business_type": "consulting", "timezone": "America/New_York"},
        headers={"Origin": "http://localhost:5173", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    offer = offering(client, default_duration_minutes=60)
    response = post(
        client,
        "/booking/reservations",
        {
            "customer_id": customer["id"],
            "offering_id": offer["id"],
            "local_start_at": "2027-01-01T10:00",
            "local_end_at": "2027-01-01T11:00",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["start_at"].startswith("2027-01-01T15:00")
    periods = post(
        client,
        "/booking/available-periods",
        {
            "offering_id": offer["id"],
            "start_at": "2027-01-02T10:00Z",
            "end_at": "2027-01-02T12:00Z",
            "step_minutes": 60,
        },
    )
    assert periods.status_code == 200, periods.text
    assert len(periods.json()["periods"]) == 2


def test_cross_tenant_ids_and_rls_are_denied(booking, database):
    a, tenant, customer, _ = booking
    offer = offering(a)
    asset = resource(a, offer)
    reservation = reserve(a, customer, offer).json()
    with TestClient(create_app()) as b:
        register_login(b)
        own = post(b, "/tenants", {"name": "Independent business"}).json()
        own_customer = post(
            b,
            "/booking/customers",
            {"full_name": "Separate customer", "email": uuid4().hex + "@example.com"},
        ).json()
        for path, identifier in (
            ("customers", customer["id"]),
            ("resources", asset["id"]),
            ("offerings", offer["id"]),
            ("reservations", reservation["id"]),
        ):
            assert b.get("/api/v1/booking/" + path + "/" + identifier).status_code == 404
        assert reserve(b, own_customer, offer).status_code == 404
        user_id = UUID(b.get("/api/v1/auth/me").json()["id"])
        with Session(database[0]) as db, db.begin():
            set_user_context(db, user_id)
            set_tenant_context(db, UUID(tenant["id"]))
            for model in (
                Customer,
                Resource,
                Offering,
                Reservation,
                ReservationResource,
                OutboxEvent,
                WaitlistEntry,
            ):
                assert db.scalars(select(model)).all() == []
        with Session(database[1]) as db, pytest.raises(DBAPIError):
            set_user_context(db, user_id)
            set_tenant_context(db, UUID(own["id"]))
            db.add(
                Reservation(
                    tenant_id=UUID(own["id"]),
                    customer_id=UUID(customer["id"]),
                    offering_id=UUID(offer["id"]),
                    start_at=datetime(2027, 1, 1, tzinfo=UTC),
                    end_at=datetime(2027, 1, 2, tzinfo=UTC),
                    status="CONFIRMED",
                    quantity=1,
                    calendar_sync_status="PENDING",
                    sync_version=1,
                )
            )
            db.flush()


def test_database_exclusion_cannot_be_disabled_by_allocation_input(booking, database):
    client, tenant, customer, _ = booking
    offer = offering(client)
    asset = resource(client, offer)
    first = reserve(client, customer, offer).json()
    user_id = UUID(client.get("/api/v1/auth/me").json()["id"])
    with Session(database[1]) as db, pytest.raises(DBAPIError) as failure:
        set_user_context(db, user_id)
        set_tenant_context(db, UUID(tenant["id"]))
        row = Reservation(
            tenant_id=UUID(tenant["id"]),
            customer_id=UUID(customer["id"]),
            offering_id=UUID(offer["id"]),
            start_at=datetime.fromisoformat(first["start_at"]),
            end_at=datetime.fromisoformat(first["end_at"]),
            status="CONFIRMED",
            quantity=1,
            calendar_sync_status="PENDING",
            sync_version=1,
        )
        db.add(row)
        db.flush()
        db.add(
            ReservationResource(
                tenant_id=row.tenant_id,
                reservation_id=row.id,
                resource_id=UUID(asset["id"]),
                allocated_quantity=1,
                allocation_status="RELEASED",
                exclusive=False,
                start_at=row.start_at,
                end_at=row.end_at,
                occupied_until=row.end_at,
            )
        )
        db.flush()
    assert getattr(failure.value.orig, "sqlstate", None) == "23P01"


def test_simultaneous_raw_sql_capacity_writes_are_guarded(booking, database):
    client, tenant, customer, _ = booking
    offer = offering(client, "CAPACITY", capacity_limit=3)
    user = UUID(client.get("/api/v1/auth/me").json()["id"])
    barrier = Barrier(2)

    def attempt(_):
        try:
            with Session(database[1]) as db, db.begin():
                set_user_context(db, user)
                set_tenant_context(db, UUID(tenant["id"]))
                row = Reservation(
                    tenant_id=UUID(tenant["id"]),
                    customer_id=UUID(customer["id"]),
                    offering_id=UUID(offer["id"]),
                    start_at=datetime(2027, 1, 1, tzinfo=UTC),
                    end_at=datetime(2027, 1, 2, tzinfo=UTC),
                    status="CONFIRMED",
                    quantity=2,
                    calendar_sync_status="PENDING",
                    sync_version=1,
                )
                barrier.wait(timeout=10)
                db.add(row)
                db.flush()
            return "committed"
        except DBAPIError as error:
            return getattr(error.orig, "sqlstate", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))
    assert sorted(results) == ["23514", "committed"]


def test_staff_can_book_but_cannot_change_catalog(booking, database):
    client, tenant, customer, _ = booking
    offer = offering(client)
    resource(client, offer)
    with database[1].begin() as db:
        db.execute(
            TenantMembership.__table__.update()
            .where(TenantMembership.tenant_id == UUID(tenant["id"]))
            .values(role="staff")
        )
    assert client.get("/api/v1/booking/resources").status_code == 200
    assert (
        post(
            client, "/booking/resources", {"name": "Forbidden", "resource_type": "asset"}
        ).status_code
        == 403
    )
    response = reserve(client, customer, offer)
    assert response.status_code == 201, response.text


def test_shared_allocation_quantity_cannot_understate_reservation(booking, database):
    client, tenant, customer, _ = booking
    offer = offering(client, "CAPACITY", capacity_limit=10)
    asset = resource(client, offer, "Desks", "desk", capacity=3, allocation_mode="SHARED")
    first = reserve(client, customer, offer, quantity=2).json()
    user = UUID(client.get("/api/v1/auth/me").json()["id"])
    with Session(database[1]) as db, pytest.raises(DBAPIError) as failure:
        set_user_context(db, user)
        set_tenant_context(db, UUID(tenant["id"]))
        row = Reservation(
            tenant_id=UUID(tenant["id"]),
            customer_id=UUID(customer["id"]),
            offering_id=UUID(offer["id"]),
            start_at=datetime.fromisoformat(first["start_at"]),
            end_at=datetime.fromisoformat(first["end_at"]),
            status="CONFIRMED",
            quantity=2,
            calendar_sync_status="PENDING",
            sync_version=1,
        )
        db.add(row)
        db.flush()
        db.add(
            ReservationResource(
                tenant_id=row.tenant_id,
                reservation_id=row.id,
                resource_id=UUID(asset["id"]),
                allocated_quantity=1,
                allocation_status="ACTIVE",
                exclusive=False,
                start_at=row.start_at,
                end_at=row.end_at,
                occupied_until=row.end_at,
            )
        )
        db.flush()
    assert getattr(failure.value.orig, "sqlstate", None) == "23514"


def test_catalog_edits_customer_consent_and_local_date_filters(booking):
    client, _, customer, _ = booking
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": csrf}
    response = client.put(
        "/api/v1/booking/customers/" + customer["id"],
        json={
            "full_name": "Updated customer",
            "email": customer["email"],
            "notification_consent": False,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["notification_consent"] is False
    offer = offering(client, default_duration_minutes=60)
    resource(client, offer)
    first = reserve(client, customer, offer)
    assert first.status_code == 201
    rows = client.get(
        "/api/v1/booking/reservations",
        params={"local_from": "2027-01-01", "local_until": "2027-01-02"},
    )
    assert rows.status_code == 200, rows.text
    assert len(rows.json()) == 1
    assert (
        client.get(
            "/api/v1/booking/reservations",
            params={"local_from": "2027-01-02", "local_until": "2027-01-03"},
        ).json()
        == []
    )
