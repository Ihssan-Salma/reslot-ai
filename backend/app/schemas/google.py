from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GoogleStatus(BaseModel):
    configured: bool
    connection_status: str
    email: str | None = None
    updated_at: datetime | None = None


class CalendarProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    google_calendar_id: str
    calendar_name: str
    timezone: str
    access_role: str
    is_selected: bool


class CalendarSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calendar_id: UUID


class EmailMetadata(BaseModel):
    id: str
    sender: str = ""
    subject: str = ""
    date: str = ""


class GmailPreview(BaseModel):
    email: str
    messages: list[EmailMetadata]


class EventPreview(BaseModel):
    id: str
    summary: str
    start: str
    end: str


class EventsPreview(BaseModel):
    calendar_name: str
    timezone: str
    events: list[EventPreview]


class OAuthStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No client-supplied redirect, tenant context, scopes, or identity parameters.
