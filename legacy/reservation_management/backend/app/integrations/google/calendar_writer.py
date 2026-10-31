from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.integrations.google.errors import GoogleError
from app.services.google import CALENDAR_URL, GoogleService


class CalendarWriter:
    """Write only events carrying this tenant/reservation's private ownership markers."""

    def __init__(self, google: GoogleService, calendar_id: str):
        self.google = google
        self.base = CALENDAR_URL + "/calendars/" + quote(calendar_id, safe="") + "/events"

    def request(
        self,
        method: str,
        event_id: str | None = None,
        body: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        connection = self.google.connected()
        token = self.google.access_token or self.google.refresh(connection)
        url = self.base + ("/" + quote(event_id, safe="") if event_id else "")
        for attempt in range(2):
            headers = {"Authorization": "Bearer " + token}
            if etag:
                headers["If-Match"] = etag
            response = self.google.http.request(
                method,
                url,
                headers=headers,
                json=body,
                params={"sendUpdates": "none"} if method != "GET" else None,
            )
            if response.status_code != 401 or attempt:
                break
            token = self.google.refresh(connection)
        status = response.status_code
        if status == 401:
            self.google.invalidate(connection)
            raise GoogleError(
                "google_reconnect_required", "Reconnect Google before retrying synchronization", 409
            )
        if status in {404, 409, 410, 412}:
            return status, {}
        if status == 403:
            raise GoogleError(
                "calendar_permission_denied", "Calendar permission is insufficient", 403
            )
        if status == 429 or status >= 500:
            raise GoogleError(
                "google_unavailable", "Calendar synchronization is temporarily unavailable", 503
            )
        if not 200 <= status < 300:
            raise GoogleError("google_api_error", "Calendar synchronization could not be completed")
        return status, {} if status == 204 or not response.content else self.google.http.json(
            response
        )

    @staticmethod
    def same_body(remote: dict[str, Any], body: dict[str, Any]) -> bool:
        def instant(value: dict[str, Any]) -> str:
            if "date" in value:
                return str(value["date"])
            try:
                return (
                    datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
                    .astimezone(UTC)
                    .isoformat()
                )
            except (ValueError, KeyError):
                return "invalid"

        return all(
            remote.get(key, "") == body.get(key, "") for key in ("summary", "description")
        ) and all(
            instant(remote.get(key, {})) == instant(body.get(key, {})) for key in ("start", "end")
        )

    @staticmethod
    def conflict() -> GoogleError:
        return GoogleError(
            "calendar_external_conflict",
            "Calendar event changed externally or is not managed by this reservation",
            409,
        )

    def synchronize(
        self,
        event_id: str,
        body: dict[str, Any],
        delete: bool,
        expected_etag: str | None,
        previous_bodies: list[dict[str, Any]],
    ) -> str | None:
        status, remote = self.request("GET", event_id)
        if status in {404, 410}:
            if delete:
                return expected_etag
            if status == 410 or expected_etag:
                raise self.conflict()
            status, created = self.request("POST", body={**body, "id": event_id})
            if status == 409:
                status, remote = self.request("GET", event_id)
                if status != 200:
                    raise self.conflict()
            elif status in {200, 201}:
                return str(created.get("etag", "")) or None
            else:
                raise self.conflict()
        if status != 200:
            raise self.conflict()
        if remote.get("status") == "cancelled":
            if delete:
                return str(remote.get("etag", expected_etag or "")) or None
            raise self.conflict()
        private = remote.get("extendedProperties", {}).get("private", {})
        desired = body["extendedProperties"]["private"]
        if any(private.get(key) != desired[key] for key in ("reslot_tenant", "reslot_reservation")):
            raise self.conflict()
        etag = remote.get("etag")
        if not etag:
            raise self.conflict()
        if (
            not delete
            and private.get("reslot_version") == desired["reslot_version"]
            and self.same_body(remote, body)
        ):
            return str(etag)
        if expected_etag:
            if etag != expected_etag:
                raise self.conflict()
        elif not any(
            self.same_body(remote, previous)
            and previous.get("extendedProperties", {}).get("private", {}).get("reslot_version")
            == private.get("reslot_version")
            for previous in previous_bodies
        ):
            raise self.conflict()
        if delete:
            status, _ = self.request("DELETE", event_id, etag=str(etag))
            if status in {200, 204, 404, 410}:
                return str(etag)
        else:
            patch = {**body, "extendedProperties": {"private": {**private, **desired}}}
            status, updated = self.request("PATCH", event_id, patch, str(etag))
            if status == 200:
                return str(updated.get("etag", "")) or None
        raise self.conflict()
