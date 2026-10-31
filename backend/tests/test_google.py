import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_auth_tenants import post, register_login

from app.core.config import get_settings
from app.core.security import digest_token
from app.db.session import set_tenant_context, set_user_context
from app.integrations.google.config import JWKS_URL, REVOKE_URL, SCOPES, TOKEN_URL, decrypt_token
from app.integrations.google.http import GoogleHTTP, get_google_http
from app.main import create_app
from app.models import BusinessCalendar, GoogleConnection, GoogleOAuthState, TenantMembership

pytestmark = pytest.mark.integration
BASE = "/api/v1/integrations/google"
CALLBACK = "/api/v1/oauth/google/callback"


class GoogleProvider:
    """Mock only the HTTP boundary; real JWT verification and encryption still execute."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.nonce = ""
        self.subject = "google-subject-one"
        self.claim_overrides = {}
        self.token_overrides = {}
        self.refresh_overrides = {}
        self.refresh_error = None
        self.exchange_error = None
        self.revocation_status = 200
        self.requests = []
        self.inbox_empty = False
        self.api_status = 200
        self.timeout = False
        self.bad_signature = False
        self.calendars = [
            {
                "id": "owner@example.com",
                "summary": "Salon appointments",
                "timeZone": "Europe/Paris",
                "accessRole": "owner",
            },
            {
                "id": "writer@example.com",
                "summary": "Shared writable",
                "timeZone": "America/New_York",
                "accessRole": "writer",
            },
            {
                "id": "reader@example.com",
                "summary": "Read-only",
                "timeZone": "UTC",
                "accessRole": "reader",
            },
        ]
        self.api_401_once = False
        self.refresh_count = 0
        self.empty_events = False
        self.paginated = False
        self.malformed_api = False
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.installed = []

    def install(self, client: TestClient) -> None:
        client.app.dependency_overrides[get_google_http] = lambda: GoogleHTTP(self.client)
        self.installed.append(client.app)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?", 1)[0]
        if self.timeout:
            raise httpx.ReadTimeout("sensitive provider body must not escape", request=request)
        if url == TOKEN_URL:
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                if self.exchange_error:
                    return httpx.Response(
                        400,
                        json={"error": self.exchange_error, "error_description": "private detail"},
                    )
                claims = {
                    "iss": "https://accounts.google.com",
                    "aud": "test-client.apps.googleusercontent.com",
                    "sub": self.subject,
                    "email": "salon@example.com",
                    "email_verified": True,
                    "iat": int(datetime.now(UTC).timestamp()),
                    "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
                    "nonce": self.nonce,
                    **self.claim_overrides,
                }
                signing_key = (
                    rsa.generate_private_key(public_exponent=65537, key_size=2048)
                    if self.bad_signature
                    else self.key
                )
                token = jwt.encode(
                    claims, signing_key, algorithm="RS256", headers={"kid": "test-key"}
                )
                return httpx.Response(
                    200,
                    json={
                        "access_token": "private-access-token",
                        "refresh_token": "private-refresh-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "scope": " ".join(SCOPES),
                        "id_token": token,
                        **self.token_overrides,
                    },
                )
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"][0].startswith("private-")
            self.refresh_count += 1
            if self.refresh_error:
                return httpx.Response(
                    400, json={"error": self.refresh_error, "error_description": "private detail"}
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "private-refreshed-access",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    **self.refresh_overrides,
                },
            )
        if url == JWKS_URL:
            key = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [{**key, "kid": "test-key", "alg": "RS256"}]})
        if url == REVOKE_URL:
            assert request.method == "POST"
            assert parse_qs(request.content.decode())["token"][0].startswith("private-")
            return httpx.Response(
                self.revocation_status,
                json={}
                if self.revocation_status == 200
                else {
                    "error": "invalid_token" if self.revocation_status == 400 else "server_error"
                },
            )
        assert request.method == "GET", "Calendar/email writes must never occur"
        assert request.headers["authorization"].startswith("Bearer private-")
        if self.api_401_once:
            self.api_401_once = False
            return httpx.Response(401, json={"error": {"message": "private detail"}})
        if self.api_status != 200:
            return httpx.Response(self.api_status, json={"error": {"message": "private detail"}})
        if self.malformed_api:
            return httpx.Response(200, text="not JSON: private detail")
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(
                200, json={"emailAddress": "salon@example.com", "messagesTotal": 99}
            )
        if path.endswith("/messages"):
            assert request.url.params["labelIds"] == "INBOX"
            assert request.url.params["maxResults"] == "5"
            return httpx.Response(
                200,
                json={} if self.inbox_empty else {"messages": [{"id": str(i)} for i in range(6)]},
            )
        if "/messages/" in path:
            assert request.url.params["format"] == "metadata"
            assert set(request.url.params.get_list("metadataHeaders")) == {
                "From",
                "Subject",
                "Date",
            }
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[1],
                    "snippet": "private-body",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Customer <customer@example.com>"},
                            {"name": "Subject", "value": "Appointment question"},
                            {"name": "Date", "value": "Sat, 31 Oct 2026 12:00:00 +0000"},
                        ],
                        "body": {"data": "private-body"},
                    },
                },
            )
        if path.endswith("/calendarList"):
            if self.paginated:
                page = request.url.params.get("pageToken")
                return httpx.Response(
                    200,
                    json={"items": self.calendars[1:]}
                    if page
                    else {"items": self.calendars[:1], "nextPageToken": "next"},
                )
            return httpx.Response(200, json={"items": self.calendars})
        if "/calendarList/" in path:
            calendar_id = path.rsplit("/", 1)[1]
            found = next((item for item in self.calendars if item["id"] == calendar_id), None)
            return (
                httpx.Response(200, json=found)
                if found
                else httpx.Response(404, json={"error": "gone"})
            )
        if path.endswith("/events"):
            assert request.url.params["maxResults"] == "5"
            assert request.url.params["singleEvents"] == "true"
            assert request.url.params["orderBy"] == "startTime"
            assert "timeMin" in request.url.params
            items = (
                []
                if self.empty_events
                else [
                    {
                        "id": "event-one",
                        "summary": "Hair appointment",
                        "start": {"dateTime": "2027-01-01T10:00:00+01:00"},
                        "end": {"dateTime": "2027-01-01T11:00:00+01:00"},
                        "description": "private description",
                        "attendees": [{"email": "private@example.com"}],
                    },
                    {
                        "id": "all-day",
                        "start": {"date": "2027-01-02"},
                        "end": {"date": "2027-01-03"},
                    },
                ]
            )
            if self.paginated:
                page = request.url.params.get("pageToken")
                return httpx.Response(
                    200,
                    json={"items": items[1:]}
                    if page
                    else {"items": items[:1], "nextPageToken": "next"},
                )
            return httpx.Response(200, json={"items": items, "timeZone": "Europe/Paris"})
        raise AssertionError("Unexpected Google HTTP endpoint")


@pytest.fixture
def google_provider(client, monkeypatch) -> Iterator[GoogleProvider]:
    provider = GoogleProvider()
    with monkeypatch.context() as patch:
        patch.setenv("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
        patch.setenv("GOOGLE_CLIENT_SECRET", "test-only-client-secret")
        patch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        get_settings.cache_clear()
        provider.install(client)
        try:
            yield provider
        finally:
            for app in provider.installed:
                app.dependency_overrides.pop(get_google_http, None)
            provider.client.close()
            get_settings.cache_clear()


@pytest.fixture
def google_account(client, google_provider):
    credentials = register_login(client)
    tenant = post(client, "/tenants", {"name": "Google Salon"}).json()
    return client, tenant, credentials


def begin(client, provider):
    response = post(client, "/integrations/google/start", {})
    assert response.status_code == 200, response.text
    params = parse_qs(urlparse(response.json()["authorization_url"]).query)
    provider.nonce = params["nonce"][0]
    return params


def finish(client, params, **kwargs):
    response = client.get(
        CALLBACK,
        params={"state": params["state"][0], "code": "test-only-code", **kwargs},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return parse_qs(urlparse(response.headers["location"]).query)["oauth"][0]


def connect(client, provider):
    params = begin(client, provider)
    assert finish(client, params) == "connected"
    return params


def test_oauth_start_binds_state_nonce_pkce_and_offline_access(
    google_account, google_provider, database
):
    client, tenant, _ = google_account
    params = begin(client, google_provider)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert set(params["scope"][0].split()) == set(SCOPES)
    assert params["code_challenge_method"] == ["S256"]
    assert "client_secret" not in params
    with Session(database[1]) as db:
        attempt = db.scalar(
            select(GoogleOAuthState).where(GoogleOAuthState.tenant_id == UUID(tenant["id"]))
        )
        assert attempt.state_hash == digest_token(params["state"][0])
        assert attempt.nonce_hash == digest_token(params["nonce"][0])
        verifier = decrypt_token(attempt.encrypted_code_verifier)
        assert verifier != attempt.encrypted_code_verifier
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert params["code_challenge"] == [challenge]


def test_real_jwt_validation_and_encrypted_storage(google_account, google_provider, database):
    client, tenant, _ = google_account
    params = connect(client, google_provider)
    status = client.get(BASE).json()
    assert status["connection_status"] == "connected"
    assert status["email"] == "salon@example.com"
    assert not any(
        secret in json.dumps(status)
        for secret in ("private-", "encrypted_refresh_token", "google_subject")
    )
    with Session(database[1]) as db:
        connection = db.scalar(
            select(GoogleConnection).where(GoogleConnection.tenant_id == UUID(tenant["id"]))
        )
        assert connection.encrypted_refresh_token != "private-refresh-token"
        assert decrypt_token(connection.encrypted_refresh_token) == "private-refresh-token"
    exchange = next(
        request for request in google_provider.requests if str(request.url) == TOKEN_URL
    )
    form = parse_qs(exchange.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert "code_verifier" in form
    assert "client_secret" in form
    assert finish(client, params) == "invalid_state"


def test_invalid_state_does_not_call_google(google_account, google_provider):
    client, _, _ = google_account
    assert finish(client, {"state": ["forged"]}) == "invalid_state"
    assert google_provider.requests == []


def test_expired_state_is_consumed(google_account, google_provider, database):
    client, tenant, _ = google_account
    params = begin(client, google_provider)
    with database[1].begin() as connection:
        connection.execute(
            update(GoogleOAuthState)
            .where(GoogleOAuthState.tenant_id == UUID(tenant["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert finish(client, params) == "expired_state"
    assert finish(client, params) == "invalid_state"
    assert google_provider.requests == []


def test_wrong_browser_session_cannot_use_state(google_account, google_provider):
    client, tenant, credentials = google_account
    params = begin(client, google_provider)
    with TestClient(create_app()) as other:
        google_provider.install(other)
        assert post(other, "/auth/login", credentials).status_code == 200
        assert post(other, "/tenants/switch", {"tenant_id": tenant["id"]}).status_code == 200
        assert finish(other, params) == "invalid_state"
    assert google_provider.requests == []
    assert finish(client, params) == "connected"


def test_callback_rechecks_membership(google_account, google_provider, database):
    client, tenant, _ = google_account
    params = begin(client, google_provider)
    with database[1].begin() as db:
        db.execute(delete(TenantMembership).where(TenantMembership.tenant_id == UUID(tenant["id"])))
    assert (
        client.get(CALLBACK, params={"state": params["state"][0], "code": "code"}).status_code
        == 404
    )
    assert google_provider.requests == []


def test_callback_rejects_changed_selected_tenant(google_account, google_provider):
    client, _, _ = google_account
    params = begin(client, google_provider)
    assert post(client, "/tenants", {"name": "Other salon"}).status_code == 201
    assert finish(client, params) == "invalid_state"
    assert google_provider.requests == []


def test_denied_and_duplicate_callbacks_are_safe(google_account, google_provider):
    client, _, _ = google_account
    params = begin(client, google_provider)
    response = client.get(
        CALLBACK,
        params=[("state", params["state"][0]), ("code", "one"), ("code", "two")],
        follow_redirects=False,
    )
    assert "oauth=invalid_callback" in response.headers["location"]
    response = client.get(
        CALLBACK,
        params={"state": params["state"][0], "error": "access_denied"},
        follow_redirects=False,
    )
    assert "oauth=access_denied" in response.headers["location"]
    assert finish(client, params) == "invalid_state"
    assert google_provider.requests == []


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "attacker-client"},
        {"aud": ["test-client.apps.googleusercontent.com", "another-client"]},
        {"iss": "https://evil.example"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"email_verified": False},
        {"azp": "wrong-client"},
    ],
)
def test_invalid_identity_rejected(google_account, google_provider, claims):
    client, _, _ = google_account
    google_provider.claim_overrides = claims
    assert finish(client, begin(client, google_provider)) == "invalid_google_identity"
    assert client.get(BASE).json()["connection_status"] == "disconnected"


def test_invalid_signature_rejected(google_account, google_provider):
    client, _, _ = google_account
    google_provider.bad_signature = True
    assert finish(client, begin(client, google_provider)) == "invalid_google_identity"


@pytest.mark.parametrize(
    "tokens,outcome",
    [
        ({"refresh_token": None}, "missing_refresh_token"),
        ({"scope": "openid email"}, "missing_scopes"),
    ],
)
def test_missing_offline_token_or_scopes(google_account, google_provider, tokens, outcome):
    client, _, _ = google_account
    google_provider.token_overrides = tokens
    params = begin(client, google_provider)
    assert finish(client, params) == outcome
    assert finish(client, params) == "invalid_state"
    assert client.get(BASE).json()["connection_status"] == "disconnected"


def test_scope_alias_accepted(google_account, google_provider):
    client, _, _ = google_account
    google_provider.token_overrides = {
        "scope": " ".join(SCOPES).replace(
            "openid email ", "openid https://www.googleapis.com/auth/userinfo.email "
        )
    }
    connect(client, google_provider)


def test_recent_inbox_metadata_and_empty_inbox(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    response = client.get(BASE + "/gmail")
    assert response.status_code == 200
    assert response.json()["email"] == "salon@example.com"
    messages = response.json()["messages"]
    assert len(messages) == 5
    assert set(messages[0]) == {"id", "sender", "subject", "date"}
    assert "private-" not in response.text
    assert "snippet" not in response.text
    google_provider.inbox_empty = True
    assert client.get(BASE + "/gmail").json()["messages"] == []


def test_revoked_refresh_token_persists_reconnect_status(google_account, google_provider, database):
    client, tenant, _ = google_account
    connect(client, google_provider)
    google_provider.refresh_error = "invalid_grant"
    response = client.get(BASE + "/gmail")
    assert response.status_code == 409
    assert response.json()["code"] == "google_reconnect_required"
    assert "private detail" not in response.text
    assert client.get(BASE).json()["connection_status"] == "reconnect_required"
    with Session(database[1]) as db:
        connection = db.scalar(
            select(GoogleConnection).where(GoogleConnection.tenant_id == UUID(tenant["id"]))
        )
        assert connection.encrypted_refresh_token is None
    google_provider.refresh_error = None
    connect(client, google_provider)
    assert client.get(BASE + "/gmail").status_code == 200


def test_expired_access_token_retries_once(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.api_401_once = True
    assert client.get(BASE + "/gmail").status_code == 200
    assert google_provider.refresh_count == 2
    google_provider.api_status = 401
    assert client.get(BASE + "/gmail").status_code == 409
    assert client.get(BASE).json()["connection_status"] == "reconnect_required"


@pytest.mark.parametrize("status,expected", [(403, 403), (429, 503), (500, 503)])
def test_google_api_errors_are_sanitized(google_account, google_provider, status, expected):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.api_status = status
    response = client.get(BASE + "/gmail")
    assert response.status_code == expected
    assert "private" not in response.text


def test_calendar_permissions_selection_events_and_empty_preview(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    rows = client.get(BASE + "/calendars").json()
    assert len(rows) == 3
    assert rows[0]["timezone"]
    reader = next(row for row in rows if row["access_role"] == "reader")
    owner = next(row for row in rows if row["access_role"] == "owner")
    writer = next(row for row in rows if row["access_role"] == "writer")
    assert (
        post(
            client, "/integrations/google/calendars/select", {"calendar_id": reader["id"]}
        ).status_code
        == 403
    )
    for row in (owner, writer):
        assert (
            post(
                client, "/integrations/google/calendars/select", {"calendar_id": row["id"]}
            ).status_code
            == 200
        )
        assert sum(row["is_selected"] for row in client.get(BASE + "/calendars").json()) == 1
    response = client.get(BASE + "/events")
    assert response.status_code == 200
    assert response.json()["events"][1]["start"] == "2027-01-02"
    assert "private" not in response.text
    google_provider.empty_events = True
    assert client.get(BASE + "/events").json()["events"] == []


def test_calendar_permission_loss_clears_selection(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    rows = client.get(BASE + "/calendars").json()
    owner = next(row for row in rows if row["access_role"] == "owner")
    assert (
        post(
            client, "/integrations/google/calendars/select", {"calendar_id": owner["id"]}
        ).status_code
        == 200
    )
    google_provider.calendars[0]["accessRole"] = "reader"
    assert client.get(BASE + "/events").status_code == 403
    assert not any(row["is_selected"] for row in client.get(BASE + "/calendars").json())


def test_deleted_calendar_is_unselected(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    rows = client.get(BASE + "/calendars").json()
    owner = next(row for row in rows if row["access_role"] == "owner")
    assert (
        post(
            client, "/integrations/google/calendars/select", {"calendar_id": owner["id"]}
        ).status_code
        == 200
    )
    google_provider.calendars.pop(0)
    assert client.get(BASE + "/events").status_code == 403
    assert all(row["id"] != owner["id"] for row in client.get(BASE + "/calendars").json())


def test_disconnect_revokes_removes_token_and_invalidates_attempts(
    google_account, google_provider, database
):
    client, tenant, _ = google_account
    connect(client, google_provider)
    params = begin(client, google_provider)
    response = post(client, "/integrations/google/disconnect", {})
    assert response.status_code == 200
    assert any(str(request.url) == REVOKE_URL for request in google_provider.requests)
    assert client.get(BASE).json()["connection_status"] == "disconnected"
    assert finish(client, params) == "invalid_state"
    assert client.get(BASE + "/gmail").status_code == 409
    with Session(database[1]) as db:
        row = db.scalar(
            select(GoogleConnection).where(GoogleConnection.tenant_id == UUID(tenant["id"]))
        )
        assert row.encrypted_refresh_token is None and row.granted_scopes == []


def test_failed_revocation_keeps_encrypted_token_for_retry(
    google_account, google_provider, database
):
    client, tenant, _ = google_account
    connect(client, google_provider)
    google_provider.revocation_status = 503
    assert post(client, "/integrations/google/disconnect", {}).status_code == 503
    assert client.get(BASE).json()["connection_status"] == "disconnect_pending"
    with Session(database[1]) as db:
        row = db.scalar(
            select(GoogleConnection).where(GoogleConnection.tenant_id == UUID(tenant["id"]))
        )
        assert decrypt_token(row.encrypted_refresh_token) == "private-refresh-token"
    assert post(client, "/integrations/google/start", {}).status_code == 409
    assert client.get(BASE + "/gmail").status_code == 409
    google_provider.revocation_status = 200
    assert post(client, "/integrations/google/disconnect", {}).status_code == 200


def test_already_revoked_token_disconnects_safely(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.revocation_status = 400
    assert post(client, "/integrations/google/disconnect", {}).status_code == 200


def test_management_requires_csrf(google_account, google_provider):
    client, _, _ = google_account
    for path in ("/start", "/disconnect", "/calendars/select"):
        assert (
            client.post(
                BASE + path, json={}, headers={"Origin": "http://localhost:5173"}
            ).status_code
            == 403
        )
    assert (
        post(client, "/integrations/google/start", {"tenant_id": str(uuid4())}).status_code == 422
    )


def test_missing_configuration_returns_safe_unavailable(
    google_account, google_provider, monkeypatch
):
    client, _, _ = google_account
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    get_settings.cache_clear()
    assert client.get(BASE).json()["configured"] is False
    assert post(client, "/integrations/google/start", {}).status_code == 503


def test_cross_tenant_api_and_rls_isolation(google_account, google_provider, database):
    a, tenant_a, _ = google_account
    connect(a, google_provider)
    client_calendar = a.get(BASE + "/calendars").json()[0]
    with TestClient(create_app()) as b:
        google_provider.install(b)
        register_login(b)
        tenant_b = post(b, "/tenants", {"name": "Other Google Salon"}).json()
        connect(b, google_provider)
        assert (
            post(
                b, "/integrations/google/calendars/select", {"calendar_id": client_calendar["id"]}
            ).status_code
            == 404
        )
        user_b = UUID(b.get("/api/v1/auth/me").json()["id"])
        runtime, owner = database
        with Session(runtime) as db, db.begin():
            assert db.scalars(select(GoogleConnection)).all() == []
            set_user_context(db, user_b)
            set_tenant_context(db, UUID(tenant_b["id"]))
            assert len(db.scalars(select(GoogleConnection)).all()) == 1
            assert db.scalars(select(BusinessCalendar)).all() == []
            set_tenant_context(db, UUID(tenant_a["id"]))
            assert db.scalars(select(GoogleConnection)).all() == []
            assert db.scalars(select(BusinessCalendar)).all() == []
            assert db.scalars(select(GoogleOAuthState)).all() == []
            result = db.execute(update(GoogleConnection).values(email="attacker@example.com"))
            assert result.rowcount == 0
        with Session(owner) as db, pytest.raises(DBAPIError):
            db.add(
                BusinessCalendar(
                    tenant_id=UUID(tenant_b["id"]),
                    google_connection_id=db.scalar(
                        select(GoogleConnection.id).where(
                            GoogleConnection.tenant_id == UUID(tenant_a["id"])
                        )
                    ),
                    google_calendar_id="bad",
                    calendar_name="bad",
                    timezone="UTC",
                    access_role="owner",
                    is_selected=False,
                )
            )
            db.flush()


def test_staff_cannot_access_google_credentials(google_account, google_provider, database):
    client, tenant, _ = google_account
    connect(client, google_provider)
    with database[1].begin() as db:
        db.execute(
            update(TenantMembership)
            .where(TenantMembership.tenant_id == UUID(tenant["id"]))
            .values(role="staff")
        )
    assert client.get(BASE).status_code == 403
    assert post(client, "/integrations/google/start", {}).status_code == 403
    user_id = UUID(client.get("/api/v1/auth/me").json()["id"])
    with Session(database[0]) as db, db.begin():
        set_user_context(db, user_id)
        set_tenant_context(db, UUID(tenant["id"]))
        assert db.scalars(select(GoogleConnection)).all() == []


def test_network_failure_is_sanitized(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.timeout = True
    response = client.get(BASE + "/gmail")
    assert response.status_code == 503
    assert "sensitive" not in response.text
    assert client.get(BASE).json()["connection_status"] == "connected"


def test_calendar_and_event_pagination(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.paginated = True
    rows = client.get(BASE + "/calendars").json()
    assert len(rows) == 3
    owner = next(row for row in rows if row["access_role"] == "owner")
    assert (
        post(
            client, "/integrations/google/calendars/select", {"calendar_id": owner["id"]}
        ).status_code
        == 200
    )
    assert len(client.get(BASE + "/events").json()["events"]) == 2


def test_code_exchange_failure_consumes_state(google_account, google_provider):
    client, _, _ = google_account
    google_provider.exchange_error = "invalid_grant"
    params = begin(client, google_provider)
    assert finish(client, params) == "code_exchange_failed"
    assert finish(client, params) == "invalid_state"


def test_refresh_rotation_is_encrypted(google_account, google_provider, database):
    client, tenant, _ = google_account
    connect(client, google_provider)
    google_provider.refresh_overrides = {"refresh_token": "private-rotated-refresh"}
    assert client.get(BASE + "/gmail").status_code == 200
    with Session(database[1]) as db:
        row = db.scalar(
            select(GoogleConnection).where(GoogleConnection.tenant_id == UUID(tenant["id"]))
        )
        assert row.encrypted_refresh_token != "private-rotated-refresh"
        assert decrypt_token(row.encrypted_refresh_token) == "private-rotated-refresh"


def test_reconnect_rejects_different_google_identity(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.subject = "another-google-subject"
    assert finish(client, begin(client, google_provider)) == "account_mismatch"
    assert client.get(BASE).json()["connection_status"] == "connected"


def test_malformed_google_response_is_sanitized(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.malformed_api = True
    response = client.get(BASE + "/gmail")
    assert response.status_code == 502
    assert "private detail" not in response.text


def test_scope_loss_requires_reconnection(google_account, google_provider):
    client, _, _ = google_account
    connect(client, google_provider)
    google_provider.refresh_overrides = {"scope": "openid email"}
    assert client.get(BASE + "/gmail").status_code == 409
    assert client.get(BASE).json()["connection_status"] == "reconnect_required"
