import copy
import json
from urllib.parse import unquote
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_auth_tenants import post, register_login
from test_google import connect
from test_google import google_account as _google_account
from test_google import google_provider as _google_provider

from app.db.session import set_tenant_context, set_user_context
from app.integrations.google.config import BASE_SCOPES
from app.models import (
    GoogleConnection,
    IdempotencyRecord,
    IntegrationCursor,
    SpreadsheetConfiguration,
    WorkflowState,
)
from app.services.google_sheets import CUSTOMER_COLUMNS, WAITLIST_COLUMNS

google_account = _google_account
google_provider = _google_provider

pytestmark = pytest.mark.integration
SHEETS = "/api/v1/integrations/sheets"
CALENDAR = "/api/v1/integrations/calendar"
FILE = "synthetic_spreadsheet_one"


class ExternalData:
    def __init__(self, provider):
        self.provider = provider
        self.customers = [
            CUSTOMER_COLUMNS.copy(),
            ["cust-1", "Alex Example", "alex@example.com", "", True],
            ["cust-2", "Sam Example", "sam@example.com", "555-0101", False],
        ]
        self.waiting = [
            WAITLIST_COLUMNS.copy(),
            [
                "entry-1",
                "cust-1",
                "Dinner",
                "2027-01-02T18:00:00+01:00",
                "2027-01-02T21:00:00+01:00",
                2,
                1,
                "Near a window",
                "WAITING",
            ],
            [
                "entry-2",
                "cust-2",
                "Overnight stay",
                "2027-01-03",
                "2027-01-05",
                "",
                2,
                "",
                "INACTIVE",
            ],
        ]
        self.status = 200
        self.puts = []
        self.write_status = 200
        self.files = []
        self.move_after_write = False
        self.lose_write_response = False
        self.event = {
            "id": "event-1",
            "etag": "etag-1",
            "summary": "Private summary must not be stored",
            "start": {"dateTime": "2027-01-02T18:00:00+01:00"},
            "end": {"dateTime": "2027-01-02T19:00:00+01:00"},
            "extendedProperties": {
                "private": {
                    "reslot_kind": "reservation",
                    "reslot_schema": "1",
                    "reslot_reference": "EXTERNAL-1",
                    "reslot_offering": "Dinner",
                    "reslot_quantity": "1",
                    "reslot_party_size": "2",
                }
            },
        }
        self.events = [self.event]
        self.event_pages = False
        original = provider.handle

        def handle(request):
            if request.url.host == "sheets.googleapis.com":
                provider.requests.append(request)
                assert request.headers["authorization"].startswith("Bearer private-")
                if self.status != 200:
                    return httpx.Response(self.status, json={"error": "private provider failure"})
                path = unquote(request.url.path)
                self.files.append(path.split("/")[3])
                if "/values/" not in path:
                    return httpx.Response(
                        200,
                        json={
                            "spreadsheetId": FILE,
                            "sheets": [
                                {"properties": {"title": name}}
                                for name in ["Customers", "WaitingList"]
                            ],
                        },
                    )
                if request.method == "GET":
                    return httpx.Response(
                        200,
                        json={"values": self.customers if "Customers" in path else self.waiting},
                    )
                assert request.method == "PUT"
                if self.write_status != 200:
                    return httpx.Response(
                        self.write_status, json={"error": "private write failure"}
                    )
                assert request.url.params["valueInputOption"] == "RAW"
                payload = json.loads(request.content)
                self.puts.append(payload)
                row = int(payload["range"].split("!")[1][1:])
                if self.move_after_write:
                    self.waiting[1], self.waiting[2] = self.waiting[2], self.waiting[1]
                self.waiting[row - 1][self.waiting[0].index("status")] = payload["values"][0][0]
                if self.lose_write_response:
                    self.lose_write_response = False
                    raise httpx.ReadTimeout("private error", request=request)
                return httpx.Response(200, json={"updatedCells": 1})
            if "/events/" in request.url.path:
                provider.requests.append(request)
                if self.status != 200:
                    return httpx.Response(self.status, json={"error": "private provider failure"})
                return httpx.Response(200, json=self.event)
            if (
                request.url.path.endswith("/events")
                and request.url.params.get("maxResults") == "2500"
            ):
                provider.requests.append(request)
                if self.event_pages and not request.url.params.get("pageToken"):
                    return httpx.Response(200, json={"items": [], "nextPageToken": "second"})
                return httpx.Response(200, json={"items": self.events})
            return original(request)

        provider.client.close()
        provider.client = httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def connected_data(google_account, google_provider):
    client, tenant, credentials = google_account
    external = ExternalData(google_provider)
    connect(client, google_provider)
    return client, tenant, credentials, external


def configure(client, spreadsheet_id=FILE):
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.put(
        SHEETS,
        json={"spreadsheet_id": spreadsheet_id},
        headers={"Origin": "http://localhost:5173", "X-CSRF-Token": token},
    )


def selected_calendar(client):
    rows = client.get("/api/v1/integrations/google/calendars").json()
    assert (
        post(
            client,
            "/integrations/google/calendars/select",
            {"calendar_id": next(row["id"] for row in rows if row["access_role"] == "owner")},
        ).status_code
        == 200
    )


def test_sheet_reads_lookups_status_write_and_idempotency(connected_data):
    client, _, _, external = connected_data
    assert configure(client).status_code == 200
    assert client.get(SHEETS + "/health").json() == {
        "healthy": True,
        "customer_count": 2,
        "waiting_list_count": 2,
    }
    assert client.get(SHEETS + "/customers/cust-1").json()["notification_consent"] is True
    assert client.get(SHEETS + "/customers/cust-2").json()["notification_consent"] is False
    assert len(client.get(SHEETS + "/customers").json()) == 2
    assert (
        client.get(SHEETS + "/waiting-list/entry-1").json()["preferred_start"]
        == "2027-01-02T17:00:00Z"
    )
    assert client.get(SHEETS + "/waiting-list/entry-2").json()["preferred_start"] == "2027-01-03"
    external.waiting[1], external.waiting[2] = external.waiting[2], external.waiting[1]
    body = {"status": "OFFERED", "idempotency_key": "stable-key-001"}
    response = post(client, "/integrations/sheets/waiting-list/entry-1/status", body)
    assert response.status_code == 200, response.text
    assert external.puts[0]["range"] == "'WaitingList'!I3"
    assert (
        post(client, "/integrations/sheets/waiting-list/entry-1/status", body).json()
        == response.json()
    )
    assert len(external.puts) == 1
    assert post(client, "/integrations/sheets/waiting-list/entry-2/status", body).status_code == 409
    assert set(external.files) == {FILE}
    for path in ["/customers/absent", "/waiting-list/absent"]:
        assert client.get(SHEETS + path).status_code == 404
    assert client.get(SHEETS + "/customers/bad.id").status_code == 422


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing_column", "invalid_spreadsheet_schema"),
        ("duplicate_column", "invalid_spreadsheet_schema"),
        ("extra_cell", "invalid_spreadsheet_schema"),
        ("duplicate_id", "duplicate_spreadsheet_id"),
        ("unknown_customer", "unknown_customer_reference"),
        ("consent", "invalid_spreadsheet_data"),
        ("date_serial", "invalid_spreadsheet_data"),
        ("naive_date", "invalid_spreadsheet_data"),
        ("backwards", "invalid_spreadsheet_data"),
        ("zero_quantity", "invalid_spreadsheet_data"),
        ("status", "invalid_spreadsheet_data"),
        ("numeric_id", "invalid_spreadsheet_data"),
    ],
)
def test_invalid_sheet_data_is_rejected_without_pii(connected_data, mutation, code):
    client, _, _, external = connected_data
    if mutation == "missing_column":
        external.customers[0].pop()
    if mutation == "duplicate_column":
        external.customers[0][-1] = "customer_id"
    if mutation == "extra_cell":
        external.customers[1].append("unexpected")
    if mutation == "duplicate_id":
        external.customers[2][0] = "cust-1"
    if mutation == "unknown_customer":
        external.waiting[1][1] = "absent"
    if mutation == "consent":
        external.customers[1][-1] = "yes"
    if mutation == "date_serial":
        external.waiting[1][3] = 45678
    if mutation == "naive_date":
        external.waiting[1][3] = "2027-01-02T18:00:00"
    if mutation == "backwards":
        external.waiting[1][4] = "2026-01-02T18:00:00Z"
    if mutation == "zero_quantity":
        external.waiting[1][6] = 0
    if mutation == "status":
        external.waiting[1][-1] = "anything"
    if mutation == "numeric_id":
        external.customers[1][0] = 1
    response = configure(client)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    assert "alex@example.com" not in response.text
    assert client.get(SHEETS).json()["configured"] is False


def test_empty_sheets_and_missing_schema(connected_data):
    client, _, _, external = connected_data
    external.customers = [CUSTOMER_COLUMNS]
    external.waiting = [WAITLIST_COLUMNS]
    assert configure(client).status_code == 200
    assert client.get(SHEETS + "/customers").json() == []
    assert client.get(SHEETS + "/waiting-list").json() == []
    external.waiting = []
    assert client.get(SHEETS + "/health").status_code == 422


@pytest.mark.parametrize("status,expected", [(403, 403), (429, 503), (500, 503), (404, 502)])
def test_google_api_failures_are_sanitized(connected_data, status, expected):
    client, _, _, external = connected_data
    external.status = status
    response = configure(client)
    assert response.status_code == expected
    assert "private provider failure" not in response.text


def test_old_google_grants_keep_gmail_but_require_sheets_reconsent(
    connected_data, database, google_provider
):
    client, tenant, _, _ = connected_data
    with Session(database[1]) as db, db.begin():
        db.execute(
            update(GoogleConnection)
            .where(GoogleConnection.tenant_id == UUID(tenant["id"]))
            .values(granted_scopes=list(BASE_SCOPES))
        )
    assert client.get("/api/v1/integrations/google/gmail").status_code == 200
    assert client.get(SHEETS).json()["requires_reconsent"] is True
    assert configure(client).json()["code"] == "sheets_reconsent_required"
    assert client.get("/api/v1/integrations/google").json()["connection_status"] == "connected"
    connect(client, google_provider)
    assert client.get(SHEETS).json()["requires_reconsent"] is False
    assert configure(client).status_code == 200
    assert client.get("/api/v1/integrations/google/gmail").status_code == 200


def test_write_response_loss_and_external_row_race(connected_data):
    client, _, _, external = connected_data
    assert configure(client).status_code == 200
    external.lose_write_response = True
    body = {"status": "OFFERED", "idempotency_key": "retry-key-001"}
    path = "/integrations/sheets/waiting-list/entry-1/status"
    assert post(client, path, body).status_code == 503
    assert post(client, path, body).status_code == 200
    assert len(external.puts) == 1
    external.move_after_write = True
    body = {"status": "ACCEPTED", "idempotency_key": "race-key-001"}
    assert post(client, path, body).json()["code"] == "sheet_changed_requires_review"
    puts = len(external.puts)
    assert post(client, path, body).status_code == 409
    assert len(external.puts) == puts


def test_tenant_isolation_auth_and_csrf(connected_data, google_provider, database):
    client, tenant, credentials, _ = connected_data
    assert configure(client).status_code == 200
    selected_calendar(client)
    assert client.get(CALENDAR + "/reservations/event-1").status_code == 200
    with Session(database[1]) as db, db.begin():
        db.add_all(
            [
                IntegrationCursor(
                    tenant_id=UUID(tenant["id"]),
                    source="calendar",
                    scope_key="test",
                    encrypted_cursor="encrypted-test-value",
                ),
                WorkflowState(
                    tenant_id=UUID(tenant["id"]),
                    workflow_key="future-1",
                    workflow_type="test",
                    status="pending",
                    state={},
                ),
                IdempotencyRecord(
                    tenant_id=UUID(tenant["id"]),
                    operation_key="future-op",
                    request_hash="0" * 64,
                    status="COMPLETED",
                    result={},
                ),
            ]
        )
    user_a = client.get("/api/v1/auth/me").json()["id"]
    tables = [
        "spreadsheet_configurations",
        "calendar_event_snapshots",
        "integration_cursors",
        "workflow_states",
        "integration_idempotency",
    ]
    with Session(database[0]) as db, db.begin():
        set_user_context(db, UUID(user_a))
        set_tenant_context(db, UUID(tenant["id"]))
        for table in tables:
            assert len(db.execute(text(f"SELECT * FROM {table}")).all()) == 1
    with TestClient(client.app) as other:
        assert other.get(SHEETS).status_code == 401
        register_login(other)
        second = post(other, "/tenants", {"name": "Hotel B", "business_type": "hotel"}).json()
        connect(other, google_provider)
        assert other.get(SHEETS).json()["configured"] is False
        assert other.get(SHEETS + "/customers").status_code == 409
        assert post(other, "/tenants/switch", {"tenant_id": tenant["id"]}).status_code == 404
        assert other.get(CALENDAR + "/reservations/event-1").status_code == 409
        assert configure(other, "synthetic_spreadsheet_two").status_code == 200
        user_b = other.get("/api/v1/auth/me").json()["id"]
        with Session(database[0]) as db, db.begin():
            set_user_context(db, UUID(user_b))
            set_tenant_context(db, UUID(tenant["id"]))
            for table in tables:
                assert db.execute(text(f"SELECT * FROM {table}")).all() == []
        with pytest.raises(DBAPIError), Session(database[0]) as db, db.begin():
            set_user_context(db, UUID(user_b))
            set_tenant_context(db, UUID(tenant["id"]))
            db.add(
                IntegrationCursor(
                    tenant_id=UUID(tenant["id"]), source="calendar", scope_key="attack"
                )
            )
            db.flush()
        with Session(database[1]) as db:
            other_connection = db.scalar(
                select(GoogleConnection.id).where(GoogleConnection.tenant_id == UUID(second["id"]))
            )
        with pytest.raises(DBAPIError), Session(database[1]) as db, db.begin():
            db.execute(
                update(SpreadsheetConfiguration)
                .where(SpreadsheetConfiguration.tenant_id == UUID(tenant["id"]))
                .values(google_connection_id=other_connection)
            )
    assert (
        client.post(SHEETS + "/waiting-list/entry-1/status", json={"status": "OFFERED"}).status_code
        == 403
    )
    assert client.put(SHEETS, json={"spreadsheet_id": FILE}).status_code == 403
    assert client.get("/api/v1/reservations").status_code == 404


def test_calendar_snapshot_and_availability(connected_data, database):
    client, tenant, _, external = connected_data
    selected_calendar(client)
    response = client.get(CALENDAR + "/reservations/event-1")
    assert response.status_code == 200, response.text
    assert response.json()["classification"] == "reservation"
    with Session(database[1]) as db:
        payload = db.scalar(
            text("SELECT normalized_payload FROM calendar_event_snapshots WHERE tenant_id=:id"),
            {"id": tenant["id"]},
        )
        assert payload["offering"] == "Dinner"
        assert "Private summary" not in json.dumps(payload)
    body = {"start_at": "2027-01-02T17:00:00Z", "end_at": "2027-01-02T18:00:00Z"}
    # Unmarked opaque meetings must also block availability.
    busy = copy.deepcopy(external.event)
    busy.pop("extendedProperties")
    external.events = [busy]
    external.event_pages = True
    assert post(client, "/integrations/calendar/availability", body).json()["available"] is False
    busy["transparency"] = "transparent"
    assert post(client, "/integrations/calendar/availability", body).json()["available"] is True
    busy.pop("transparency")
    busy["status"] = "cancelled"
    assert post(client, "/integrations/calendar/availability", body).json()["available"] is True
    busy.pop("status")
    busy["end"] = {"dateTime": "2027-01-02T17:00:00Z"}
    busy["start"] = {"dateTime": "2027-01-02T16:00:00Z"}
    assert post(client, "/integrations/calendar/availability", body).json()["available"] is True
    busy["start"] = {}
    assert (
        post(client, "/integrations/calendar/availability", body).json()["requires_review"] is True
    )
    external.event.pop("extendedProperties")
    body["exclude_event_id"] = "event-1"
    assert post(client, "/integrations/calendar/availability", body).status_code == 409


def test_legacy_tables_preserved_but_inaccessible(database):
    with database[1].connect() as db:
        assert db.scalar(text("SELECT to_regclass('public.reservations')")) == "reservations"
    with database[0].connect() as db:
        assert (
            db.scalar(text("SELECT has_table_privilege(current_user,'reservations','SELECT')"))
            is False
        )
        assert (
            db.scalar(
                text(
                    "SELECT has_function_privilege(current_user,"
                    "'public.claim_calendar_work(integer)','EXECUTE')"
                )
            )
            is False
        )


@pytest.mark.parametrize("status,expected", [(403, 403), (401, 409), (429, 503), (500, 503)])
def test_sheet_write_failures(connected_data, status, expected):
    client, _, _, external = connected_data
    assert configure(client).status_code == 200
    external.write_status = status
    response = post(
        client,
        "/integrations/sheets/waiting-list/entry-1/status",
        {"status": "OFFERED", "idempotency_key": "failed-write-001"},
    )
    assert response.status_code == expected
    assert "private write failure" not in response.text


def test_revoked_access_and_changed_account(connected_data, google_provider, database):
    client, tenant, _, _ = connected_data
    assert configure(client).status_code == 200
    with Session(database[1]) as db, db.begin():
        db.execute(
            update(GoogleConnection)
            .where(GoogleConnection.tenant_id == UUID(tenant["id"]))
            .values(google_subject="different-google-subject")
        )
    assert client.get(SHEETS + "/health").json()["code"] == "spreadsheet_account_changed"
    google_provider.refresh_error = "invalid_grant"
    assert client.get(SHEETS + "/health").json()["code"] == "google_reconnect_required"


def test_reordered_columns_are_resolved_and_invalid_status_is_rejected(connected_data):
    client, _, _, external = connected_data
    external.waiting = [list(reversed(row)) for row in external.waiting]
    assert configure(client).status_code == 200
    response = post(
        client, "/integrations/sheets/waiting-list/entry-1/status", {"status": "OFFERED"}
    )
    assert response.status_code == 200
    assert external.puts[0]["range"] == "'WaitingList'!A2"
    assert (
        post(
            client, "/integrations/sheets/waiting-list/entry-1/status", {"status": "INVALID"}
        ).status_code
        == 422
    )


def test_google_calendar_failures_and_permission_loss(connected_data, google_provider):
    client, _, _, external = connected_data
    selected_calendar(client)
    external.status = 503
    assert client.get(CALENDAR + "/reservations/event-1").status_code == 503
    external.status = 200
    google_provider.calendars[0]["accessRole"] = "reader"
    assert client.get(CALENDAR + "/reservations/event-1").status_code == 403


def test_recorded_snapshot_preserves_metadata_after_deletion_tombstone(connected_data, database):
    """A previously recorded reservation must retain recoverable last-known metadata."""
    client, tenant, _, external = connected_data
    selected_calendar(client)
    assert client.get(CALENDAR + "/reservations/event-1").status_code == 200
    query = text(
        "SELECT normalized_payload FROM calendar_event_snapshots "
        "WHERE tenant_id=:tenant AND event_id='event-1'"
    )
    with Session(database[1]) as db:
        recorded = db.scalar(query, {"tenant": tenant["id"]})
    assert recorded["reservation_reference"] == "EXTERNAL-1"
    external.event = {"id": "event-1", "status": "cancelled"}
    assert client.get(CALENDAR + "/reservations/event-1").status_code == 200
    with Session(database[1]) as db:
        after_deletion = db.scalar(query, {"tenant": tenant["id"]})
    assert after_deletion == recorded, "Deletion tombstone erased recoverable reservation metadata"
