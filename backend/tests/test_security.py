import pytest
from fastapi import Response
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security import csrf_cookie_name, csrf_token, session_cookie_name, set_private_cookie


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(ValidationError, match="Secure cookies"):
        Settings(_env_file=None, app_env="production", cookie_secure=False)


def test_secure_host_cookie_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setenv("COOKIE_SECURE", "true")
        get_settings.cache_clear()
        try:
            response = Response()
            set_private_cookie(response, session_cookie_name(), "opaque-test-token")
            header = response.headers["set-cookie"]
            assert session_cookie_name() == "__Host-reslot_session"
            assert csrf_cookie_name() == "__Host-reslot_csrf"
            assert all(flag in header for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"))
            assert "Domain=" not in header
        finally:
            get_settings.cache_clear()


def test_csrf_is_bound_to_session() -> None:
    assert csrf_token("random-nonce", "session-one") != csrf_token("random-nonce", "session-two")


def test_oauth_callback_queries_are_redacted_from_access_logs() -> None:
    import logging

    from app.core.logging import RedactOAuthQuery

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "local",
            "GET",
            "/api/v1/oauth/google/callback?code=private-code&state=private-state",
            "1.1",
            303,
        ),
        None,
    )
    assert RedactOAuthQuery().filter(record)
    assert "private" not in record.getMessage()
    assert "/api/v1/oauth/google/callback" in record.getMessage()
