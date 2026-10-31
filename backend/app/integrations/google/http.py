from collections.abc import Iterator
from typing import Any

import httpx

from app.integrations.google.errors import GoogleError


class GoogleHTTP:
    def __init__(self, client: httpx.Client):
        self.client = client

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self.client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise GoogleError(
                "google_unavailable", "Google is temporarily unavailable. Please retry.", 503
            ) from None

    def json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise GoogleError(
                "invalid_google_response", "Google returned an invalid response"
            ) from None
        if not isinstance(payload, dict):
            raise GoogleError("invalid_google_response", "Google returned an invalid response")
        return payload


def get_google_http() -> Iterator[GoogleHTTP]:
    # Redirects stay disabled: bearer tokens must never follow a provider redirect to another host.
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        yield GoogleHTTP(client)
