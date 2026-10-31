import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

StableId = str
WAITLIST_STATUSES = {
    "WAITING",
    "OFFERED",
    "ACCEPTED",
    "DECLINED",
    "CANCELLED",
    "EXPIRED",
    "INACTIVE",
}
WaitlistStatus = Literal[
    "WAITING", "OFFERED", "ACCEPTED", "DECLINED", "CANCELLED", "EXPIRED", "INACTIVE"
]


def stable_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise ValueError("Use a stable text ID containing letters, digits, hyphens, or underscores")
    return value


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SpreadsheetInput(StrictInput):
    spreadsheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{10,200}$")


class SpreadsheetStatus(BaseModel):
    configured: bool
    spreadsheet_id: str | None = None
    requires_reconsent: bool = False


class CustomerRecord(StrictInput):
    customer_id: str
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)
    phone: str | None = Field(default=None, max_length=50)
    notification_consent: bool

    @field_validator("customer_id", mode="before")
    @classmethod
    def valid_id(cls, value: object) -> str:
        return stable_id(value)

    @field_validator("notification_consent", mode="before")
    @classmethod
    def consent(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value in {"TRUE", "FALSE", "true", "false"}:
            return value.lower() == "true"
        raise ValueError("Consent must be a checkbox boolean or TRUE/FALSE")

    @field_validator("phone", mode="before")
    @classmethod
    def phone_text(cls, value: object) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("Phone numbers must be stored as text")
        return value


class WaitingListRecord(StrictInput):
    entry_id: str
    customer_id: str
    requested_offering: str = Field(min_length=1, max_length=200)
    preferred_start: date | datetime
    preferred_end: date | datetime
    party_size: int | None = None
    quantity: int = Field(ge=1, le=1000000)
    additional_preferences: str = Field(default="", max_length=4000)
    status: WaitlistStatus

    @field_validator("entry_id", "customer_id", mode="before")
    @classmethod
    def valid_id(cls, value: object) -> str:
        return stable_id(value)

    @field_validator("preferred_start", "preferred_end", mode="before")
    @classmethod
    def dates(cls, value: object) -> date | datetime:
        if not isinstance(value, str):
            raise ValueError("Dates must be ISO 8601 text, not spreadsheet date serials")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Date-times require an explicit UTC offset")
        return parsed.astimezone(UTC)

    @field_validator("party_size", "quantity", mode="before")
    @classmethod
    def positive_integer(cls, value: object, info: object) -> int | None:
        if value in (None, ""):
            return None
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
            raise ValueError("Capacity values must be positive whole numbers")
        number = int(str(value))
        if not 1 <= number <= 1000000:
            raise ValueError("Capacity values must be positive")
        return number

    @model_validator(mode="after")
    def window(self) -> "WaitingListRecord":
        if isinstance(self.preferred_start, datetime) != isinstance(self.preferred_end, datetime):
            raise ValueError("Use two local dates or two offset date-times")
        if self.preferred_end <= self.preferred_start:
            raise ValueError("Preferred end must follow start")
        return self


class WaitlistStatusInput(StrictInput):
    status: WaitlistStatus
    idempotency_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,100}$")


class ReservationMetadata(StrictInput):
    reservation_reference: str = Field(min_length=1, max_length=200)
    offering: str = Field(min_length=1, max_length=200)
    resource_label: str | None = Field(default=None, max_length=200)
    party_size: int | None = Field(default=None, ge=1, le=1000000)
    quantity: int = Field(ge=1, le=1000000)
    additional_requirements: str = Field(default="", max_length=4000)
    customer_reference: str | None = None

    @field_validator("party_size", "quantity", mode="before")
    @classmethod
    def whole_number(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value))
        ):
            raise ValueError("Capacity values must be positive whole numbers")
        return value

    @field_validator("customer_reference")
    @classmethod
    def customer_id(cls, value: str | None) -> str | None:
        return stable_id(value) if value is not None else None


class NormalizedReservation(ReservationMetadata):
    calendar_id: str
    event_id: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    all_day: bool = False


class EventNormalization(BaseModel):
    calendar_id: str
    event_id: str
    classification: Literal["reservation", "review", "ignored"]
    source_status: str = "confirmed"
    reservation: NormalizedReservation | None = None
    review_reasons: list[str] = Field(default_factory=list)


class CalendarAvailabilityInput(StrictInput):
    start_at: AwareDatetime
    end_at: AwareDatetime
    exclude_event_id: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def window(self) -> "CalendarAvailabilityInput":
        if self.end_at <= self.start_at:
            raise ValueError("End must follow start")
        if self.end_at - self.start_at > timedelta(days=31):
            raise ValueError("Availability queries are limited to 31 days")
        return self


class CalendarAvailability(BaseModel):
    calendar_id: str
    available: bool
    requires_review: bool = False
    conflicting_event_ids: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
