from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.dependencies import CurrentTenant, CurrentUser, Db, TenantContext
from app.core.config import get_settings
from app.core.security import require_csrf
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP, get_google_http
from app.schemas.google import (
    CalendarProfile,
    CalendarSelection,
    EventsPreview,
    GmailPreview,
    GoogleStatus,
    OAuthStart,
)
from app.services.google import GoogleService

router = APIRouter(tags=["google integrations"])


def manager(context: CurrentTenant) -> TenantContext:
    if context.membership.role not in {"owner", "admin"}:
        raise HTTPException(403, "Only business owners and admins can manage Google integrations")
    return context


Manager = Annotated[TenantContext, Depends(manager)]
GoogleClient = Annotated[GoogleHTTP, Depends(get_google_http)]


def safe_response[T](operation: Callable[[], T]) -> T | JSONResponse:
    # Catch inside the transaction so consumed state and revocation status persist on API errors.
    try:
        return operation()
    except GoogleError as exc:
        return JSONResponse(
            status_code=exc.status, content={"detail": exc.detail, "code": exc.code}
        )


@router.get("/api/v1/integrations/google", response_model=GoogleStatus)
def status(context: Manager, db: Db, http: GoogleClient) -> GoogleStatus | JSONResponse:
    return safe_response(lambda: GoogleService(db, context, http).status())


@router.post(
    "/api/v1/integrations/google/start",
    response_model=dict[str, str],
    dependencies=[Depends(require_csrf)],
)
def start(
    data: OAuthStart, auth: CurrentUser, context: Manager, db: Db, http: GoogleClient
) -> dict[str, str] | JSONResponse:
    return safe_response(
        lambda: {"authorization_url": GoogleService(db, context, http).start(auth)}
    )


@router.get("/api/v1/oauth/google/callback")
def callback(
    request: Request, auth: CurrentUser, context: Manager, db: Db, http: GoogleClient
) -> RedirectResponse:
    outcome = "connected"
    try:
        query = request.query_params
        if any(len(query.getlist(key)) > 1 for key in ("state", "code", "error")) or (
            query.get("code") and query.get("error")
        ):
            raise GoogleError("invalid_callback", "Invalid Google callback", 400)
        GoogleService(db, context, http).finish(
            auth, query.get("state", ""), query.get("code"), query.get("error")
        )
    except GoogleError as exc:
        outcome = exc.code
    # The redirect is server configured, never supplied through state or query parameters.
    destination = get_settings().cors_origins[0] + "/settings/google?oauth=" + outcome
    response = RedirectResponse(destination, status_code=303)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/v1/integrations/google/gmail", response_model=GmailPreview)
def gmail(context: Manager, db: Db, http: GoogleClient) -> GmailPreview | JSONResponse:
    return safe_response(lambda: GoogleService(db, context, http).inbox())


@router.get("/api/v1/integrations/google/calendars", response_model=list[CalendarProfile])
def calendars(context: Manager, db: Db, http: GoogleClient) -> list[CalendarProfile] | JSONResponse:
    return safe_response(lambda: GoogleService(db, context, http).calendars())


@router.post(
    "/api/v1/integrations/google/calendars/select",
    response_model=CalendarProfile,
    dependencies=[Depends(require_csrf)],
)
def select_calendar(
    data: CalendarSelection, context: Manager, db: Db, http: GoogleClient
) -> CalendarProfile | JSONResponse:
    return safe_response(lambda: GoogleService(db, context, http).select_calendar(data.calendar_id))


@router.get("/api/v1/integrations/google/events", response_model=EventsPreview)
def events(context: Manager, db: Db, http: GoogleClient) -> EventsPreview | JSONResponse:
    return safe_response(lambda: GoogleService(db, context, http).events())


@router.post(
    "/api/v1/integrations/google/disconnect",
    response_model=dict[str, str],
    dependencies=[Depends(require_csrf)],
)
def disconnect(context: Manager, db: Db, http: GoogleClient) -> dict[str, str] | JSONResponse:
    def operation() -> dict[str, str]:
        GoogleService(db, context, http).disconnect()
        return {"connection_status": "disconnected"}

    return safe_response(operation)
