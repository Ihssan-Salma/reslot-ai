import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import AuthContext, TenantContext
from app.core.security import digest_token
from app.integrations.google.config import (
    AUTHORIZATION_URL,
    BASE_SCOPES,
    REVOKE_URL,
    SCOPES,
    TOKEN_URL,
    configured,
    decrypt_token,
    encrypt_token,
    normalize_scopes,
    require_configuration,
)
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP
from app.integrations.google.identity import verify_identity
from app.models.google import BusinessCalendar, GoogleConnection, GoogleOAuthState
from app.repositories.google import GoogleRepository
from app.schemas.google import (
    CalendarProfile,
    EmailMetadata,
    EventPreview,
    EventsPreview,
    GmailPreview,
    GoogleStatus,
)

GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3"
WRITABLE = {"owner", "writer"}


class GoogleService:
    def __init__(self, db: Session, tenant: TenantContext, http: GoogleHTTP):
        self.db = db
        self.tenant = tenant.tenant
        self.http = http
        self.repository = GoogleRepository(db, tenant.tenant.id)
        self.repository.lock()
        self.access_token: str | None = None

    def status(self) -> GoogleStatus:
        connection = self.repository.connection()
        return GoogleStatus(
            configured=configured(),
            connection_status=connection.connection_status if connection else "disconnected",
            email=connection.email
            if connection and connection.connection_status != "disconnected"
            else None,
            updated_at=connection.updated_at if connection else None,
        )

    def start(self, auth: AuthContext) -> str:
        require_configuration()
        connection = self.repository.connection()
        if connection and connection.connection_status == "disconnect_pending":
            raise GoogleError(
                "disconnect_pending", "Retry disconnecting before reconnecting Google", 409
            )
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self.repository.clear_states()
        self.db.add(
            GoogleOAuthState(
                tenant_id=self.tenant.id,
                user_id=auth.user.id,
                session_id=auth.login.id,
                state_hash=digest_token(state),
                nonce_hash=digest_token(nonce),
                encrypted_code_verifier=encrypt_token(verifier),
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        params = {
            "client_id": get_settings().google_client_id,
            "redirect_uri": get_settings().google_redirect_uri,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "scope": " ".join(SCOPES),
            "state": state,
            "nonce": nonce,
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
            "code_challenge_method": "S256",
            "include_granted_scopes": "true",
        }
        if connection and connection.connection_status != "disconnected":
            params["login_hint"] = connection.email
        self.db.flush()
        return AUTHORIZATION_URL + "?" + urlencode(params)

    def finish(self, auth: AuthContext, state: str, code: str | None, error: str | None) -> None:
        require_configuration()
        attempt = self.repository.state(digest_token(state)) if state else None
        if (
            not attempt
            or attempt.user_id != auth.user.id
            or attempt.session_id != auth.login.id
            or attempt.consumed_at is not None
        ):
            raise GoogleError("invalid_state", "Invalid or already used Google OAuth state", 400)
        attempt.consumed_at = datetime.now(UTC)
        if attempt.expires_at <= datetime.now(UTC):
            raise GoogleError(
                "expired_state", "Google authorization expired. Please connect again.", 400
            )
        if error:
            raise GoogleError("access_denied", "Google authorization was declined", 400)
        if not code:
            raise GoogleError(
                "invalid_callback", "Google did not return an authorization code", 400
            )
        settings = get_settings()
        response = self.http.request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "redirect_uri": settings.google_redirect_uri,
                "code": code,
                "code_verifier": decrypt_token(attempt.encrypted_code_verifier),
                "grant_type": "authorization_code",
            },
        )
        if response.status_code != 200:
            raise GoogleError(
                "code_exchange_failed",
                "Google authorization could not be completed",
                400 if response.status_code == 400 else 502,
            )
        payload = self.http.json(response)
        identity = verify_identity(str(payload.get("id_token", "")), attempt.nonce_hash, self.http)
        scopes = normalize_scopes(str(payload.get("scope", "")))
        if not set(SCOPES).issubset(scopes):
            raise GoogleError(
                "missing_scopes", "Grant all requested permissions to connect Google", 400
            )
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise GoogleError(
                "missing_refresh_token",
                "Google did not grant offline access. Reconnect with consent.",
                400,
            )
        self.validate_access_token(payload)
        connection = self.repository.connection()
        if connection and connection.connection_status == "disconnect_pending":
            raise GoogleError("disconnect_pending", "Finish disconnecting before reconnecting", 409)
        if (
            connection
            and connection.connection_status != "disconnected"
            and connection.google_subject != identity["sub"]
        ):
            raise GoogleError(
                "account_mismatch",
                "Disconnect the existing Google account before choosing a different account",
                409,
            )
        self.repository.clear_calendars()
        if connection is None:
            connection = GoogleConnection(tenant_id=self.tenant.id)
            self.db.add(connection)
        connection.google_subject = str(identity["sub"])
        connection.email = str(identity["email"]).lower()
        connection.encrypted_refresh_token = encrypt_token(refresh_token)
        connection.granted_scopes = scopes
        connection.connection_status = "connected"
        self.db.flush()

    def connected(self) -> GoogleConnection:
        require_configuration()
        connection = self.repository.connection()
        if (
            not connection
            or connection.connection_status != "connected"
            or not connection.encrypted_refresh_token
        ):
            raise GoogleError(
                "google_reconnect_required", "Connect or reconnect Google to continue", 409
            )
        return connection

    def invalidate(self, connection: GoogleConnection) -> None:
        connection.connection_status = "reconnect_required"
        connection.encrypted_refresh_token = None
        self.access_token = None
        self.repository.clear_calendars()

    @staticmethod
    def validate_access_token(payload: dict[str, Any]) -> str:
        token = payload.get("access_token")
        if (
            not isinstance(token, str)
            or not token
            or str(payload.get("token_type", "")).lower() != "bearer"
            or not isinstance(payload.get("expires_in"), int)
            or payload["expires_in"] <= 0
        ):
            raise GoogleError(
                "invalid_google_response", "Google did not return a valid access token"
            )
        return token

    def refresh(self, connection: GoogleConnection) -> str:
        settings = get_settings()
        response = self.http.request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "refresh_token": decrypt_token(connection.encrypted_refresh_token or ""),
                "grant_type": "refresh_token",
            },
        )
        payload = self.http.json(response)
        if response.status_code != 200:
            if payload.get("error") == "invalid_grant":
                self.invalidate(connection)
                raise GoogleError(
                    "google_reconnect_required",
                    "Google access was revoked or expired. Reconnect your account.",
                    409,
                )
            raise GoogleError(
                "google_unavailable", "Google token refresh failed. Please retry.", 503
            )
        if "scope" in payload and not set(BASE_SCOPES).issubset(
            normalize_scopes(str(payload["scope"]))
        ):
            self.invalidate(connection)
            raise GoogleError(
                "missing_scopes", "Google permissions changed. Reconnect your account.", 409
            )
        if "scope" in payload:
            connection.granted_scopes = normalize_scopes(str(payload["scope"]))
        token = self.validate_access_token(payload)
        if payload.get("refresh_token"):
            connection.encrypted_refresh_token = encrypt_token(str(payload["refresh_token"]))
        self.access_token = token
        return token

    def get(
        self,
        url: str,
        params: Any = None,
        *,
        missing_ok: bool = False,
        identify_missing: bool = False,
    ) -> dict[str, Any]:
        connection = self.connected()
        token = self.access_token or self.refresh(connection)
        response = self.http.request(
            "GET", url, headers={"Authorization": "Bearer " + token}, params=params
        )
        if response.status_code == 401:
            token = self.refresh(connection)
            response = self.http.request(
                "GET", url, headers={"Authorization": "Bearer " + token}, params=params
            )
        if response.status_code == 401:
            self.invalidate(connection)
            raise GoogleError(
                "google_reconnect_required",
                "Google access is no longer valid. Reconnect your account.",
                409,
            )
        if response.status_code == 404 and missing_ok:
            return {"deleted": True, "accessRole": "none"}
        if identify_missing and response.status_code in (404, 410):
            raise GoogleError("google_resource_not_found", "Google resource not found", 404)
        if response.status_code == 403:
            raise GoogleError(
                "google_permission_denied",
                "Google denied this operation. Check account permissions.",
                403,
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise GoogleError(
                "google_unavailable", "Google is temporarily unavailable. Please retry.", 503
            )
        if response.status_code != 200:
            raise GoogleError("google_api_error", "Google could not complete the request")
        return self.http.json(response)

    def inbox(self) -> GmailPreview:
        profile = self.get(GMAIL_URL + "/profile", {"fields": "emailAddress"})
        email = str(profile.get("emailAddress", ""))
        if not email:
            raise GoogleError(
                "gmail_unavailable", "No Gmail mailbox is available for this Google account", 409
            )
        messages = self.get(
            GMAIL_URL + "/messages",
            {
                "labelIds": "INBOX",
                "maxResults": 5,
                "fields": "messages(id)",
            },
        ).get("messages", [])
        result = []
        for message in messages[:5]:
            item = self.get(
                GMAIL_URL + "/messages/" + quote(str(message["id"]), safe=""),
                [
                    ("format", "metadata"),
                    ("metadataHeaders", "From"),
                    ("metadataHeaders", "Subject"),
                    ("metadataHeaders", "Date"),
                    ("fields", "id,payload(headers)"),
                ],
                missing_ok=True,
            )
            if item.get("deleted"):
                continue
            headers = {
                str(h.get("name", "")).lower(): str(h.get("value", ""))
                for h in item.get("payload", {}).get("headers", [])
            }
            result.append(
                EmailMetadata(
                    id=str(item.get("id", message["id"])),
                    sender=headers.get("from", ""),
                    subject=headers.get("subject", ""),
                    date=headers.get("date", ""),
                )
            )
        return GmailPreview(email=email, messages=result)

    def calendars(self) -> list[CalendarProfile]:
        connection = self.connected()
        items = []
        page = ""
        for _ in range(100):
            data = self.get(
                CALENDAR_URL + "/users/me/calendarList",
                {
                    "maxResults": 250,
                    "pageToken": page,
                    "fields": "nextPageToken,items(id,summary,timeZone,accessRole,deleted)",
                },
            )
            items.extend(data.get("items", []))
            page = str(data.get("nextPageToken", ""))
            if not page:
                break
        else:
            raise GoogleError(
                "calendar_limit_exceeded", "Too many calendars to display safely", 409
            )
        existing = {row.google_calendar_id: row for row in self.repository.calendars()}
        seen = set()
        for item in items:
            if item.get("deleted") or not item.get("id"):
                continue
            remote_id = str(item["id"])
            seen.add(remote_id)
            row = existing.get(remote_id)
            if row is None:
                row = BusinessCalendar(
                    tenant_id=self.tenant.id,
                    google_connection_id=connection.id,
                    google_calendar_id=remote_id,
                    is_selected=False,
                )
                self.db.add(row)
            row.calendar_name = str(item.get("summary", remote_id))
            row.timezone = str(item.get("timeZone", self.tenant.timezone))
            row.access_role = str(item.get("accessRole", "none"))
            if row.access_role not in WRITABLE:
                row.is_selected = False
        for remote_id, row in existing.items():
            if remote_id not in seen:
                self.db.delete(row)
        self.db.flush()
        return [CalendarProfile.model_validate(row) for row in self.repository.calendars()]

    def check_calendar(self, row: BusinessCalendar) -> None:
        try:
            details = self.get(
                CALENDAR_URL + "/users/me/calendarList/" + quote(row.google_calendar_id, safe=""),
                missing_ok=True,
            )
        except GoogleError as exc:
            if exc.status == 403:
                row.is_selected = False
            raise
        role = str(details.get("accessRole", "none"))
        if details.get("deleted") or role not in WRITABLE:
            row.is_selected = False
            row.access_role = role
            raise GoogleError(
                "calendar_not_writable", "Choose a calendar with writer or owner access", 403
            )
        row.access_role = role
        row.calendar_name = str(details.get("summary", row.calendar_name))
        row.timezone = str(details.get("timeZone", row.timezone))

    def select_calendar(self, calendar_id: UUID) -> CalendarProfile:
        self.connected()
        row = next((row for row in self.repository.calendars() if row.id == calendar_id), None)
        if row is None:
            raise GoogleError("calendar_not_found", "Calendar not found for this business", 404)
        self.check_calendar(row)
        for other in self.repository.calendars():
            other.is_selected = False
        self.db.flush()
        row.is_selected = True
        self.db.flush()
        return CalendarProfile.model_validate(row)

    def events(self) -> EventsPreview:
        self.connected()
        row = next((row for row in self.repository.calendars() if row.is_selected), None)
        if row is None:
            raise GoogleError(
                "calendar_not_selected", "Select a writable business calendar first", 409
            )
        self.check_calendar(row)
        items: list[dict[str, Any]] = []
        page = ""
        now = datetime.now(UTC).isoformat()
        for _ in range(100):
            data = self.get(
                CALENDAR_URL + "/calendars/" + quote(row.google_calendar_id, safe="") + "/events",
                {
                    "maxResults": 5,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "showDeleted": "false",
                    "timeMin": now,
                    "timeZone": row.timezone,
                    "pageToken": page,
                    "fields": "nextPageToken,items(id,summary,start,end)",
                },
            )
            items.extend(data.get("items", []))
            page = str(data.get("nextPageToken", ""))
            if len(items) >= 5 or not page:
                break
        else:
            raise GoogleError(
                "calendar_limit_exceeded", "Too many event pages to preview safely", 409
            )
        events = [
            EventPreview(
                id=str(item.get("id", "")),
                summary=str(item.get("summary", "Busy")),
                start=str(
                    item.get("start", {}).get("dateTime", item.get("start", {}).get("date", ""))
                ),
                end=str(item.get("end", {}).get("dateTime", item.get("end", {}).get("date", ""))),
            )
            for item in items[:5]
        ]
        return EventsPreview(calendar_name=row.calendar_name, timezone=row.timezone, events=events)

    def disconnect(self) -> None:
        connection = self.repository.connection()
        self.repository.clear_states()
        self.repository.clear_calendars()
        if connection is None:
            return
        if connection.encrypted_refresh_token:
            token = decrypt_token(connection.encrypted_refresh_token)
            connection.connection_status = "disconnect_pending"
            response = self.http.request("POST", REVOKE_URL, data={"token": token})
            if response.status_code != 200:
                error = self.http.json(response).get("error")
                if response.status_code != 400 or error != "invalid_token":
                    raise GoogleError(
                        "revocation_pending",
                        "Google revocation could not be confirmed. Retry Disconnect.",
                        503,
                    )
        connection.encrypted_refresh_token = None
        connection.granted_scopes = []
        connection.connection_status = "disconnected"
