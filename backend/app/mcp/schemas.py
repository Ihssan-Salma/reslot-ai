"""Public tool contracts. External text is data, never workflow instructions."""

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.schemas.automation import (
    CalendarAvailabilityInput,
    EventNormalization,
    StrictInput,
    WaitingListRecord,
)

EventID = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"^[A-Za-z0-9_-]+$")]
RecordID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")]
MessageID = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")]
Label = Annotated[str, Field(min_length=1, max_length=200)]
Capacity = Annotated[StrictInt, Field(ge=1, le=1000000)]


class EventInput(StrictInput):
    event_id: EventID


class TimeWindow(StrictInput):
    start_at: AwareDatetime
    end_at: AwareDatetime

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def iso_timestamp(cls, value: object) -> object:
        if isinstance(value, (int, float, bool)):
            raise ValueError("Use ISO 8601 timestamps")
        return value

    @model_validator(mode="after")
    def window(self) -> "TimeWindow":
        CalendarAvailabilityInput(start_at=self.start_at, end_at=self.end_at)
        return self


class AvailabilityInput(TimeWindow):
    resource_label: Label | None = None


class CandidatesInput(TimeWindow):
    offering: Label
    party_size: Capacity | None = None
    quantity: Capacity | None = None


class CustomerInput(StrictInput):
    customer_id: RecordID


class EntryInput(StrictInput):
    entry_id: RecordID


class EmailInput(StrictInput):
    message_id: MessageID


class Profile(BaseModel):
    customer_id: str
    full_name: str
    email: EmailStr
    notification_consent: bool


class Candidate(BaseModel):
    entry_id: str
    customer_id: str
    party_size: int | None
    quantity: int
    requires_review: bool


class Candidates(BaseModel):
    candidates: list[Candidate]
    # Free-text preferences cannot be verified automatically by this read-only tool.
    policy_version: Literal["1"] = "1"


class Availability(BaseModel):
    calendar_available: bool
    resource_available: bool | None = None
    conflicting_event_ids: list[str]
    requires_review: bool
    review_reasons: list[str]


class EmailMessage(BaseModel):
    message_id: str
    thread_id: str
    sender: str | None
    subject: str | None
    received_at: AwareDatetime | None
    text: str
    truncated: bool
    untrusted_content: Literal[True] = True


Category = Literal[
    "OK", "NOT_FOUND", "INVALID_INPUT", "ACCESS_DENIED", "GOOGLE_API_ERROR", "REVIEW_REQUIRED"
]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    message: str
    data: (
        EventNormalization
        | Availability
        | Candidates
        | Profile
        | WaitingListRecord
        | EmailMessage
        | None
    ) = None


INPUTS: dict[str, type[BaseModel]] = {
    "get_calendar_event": EventInput,
    "check_calendar_availability": AvailabilityInput,
    "get_waitlist_candidates": CandidatesInput,
    "get_customer_profile": CustomerInput,
    "get_waitlist_entry": EntryInput,
    "get_email_message": EmailInput,
}
