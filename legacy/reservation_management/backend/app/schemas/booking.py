from datetime import date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

Mode = Literal["TIMED", "NIGHTLY", "CAPACITY"]
Status = Literal["PENDING", "CONFIRMED", "CANCELLED", "COMPLETED", "NO_SHOW"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequirementWrite(Input):
    resource_type: str = Field(min_length=1, max_length=80)
    required_count: int = Field(default=1, ge=1, le=20)


class PolicyWrite(Input):
    require_resources: bool = False
    check_in_time: time = time(15)
    check_out_time: time = time(11)
    min_nights: int = Field(default=1, ge=1, le=3650)
    max_nights: int | None = Field(default=None, ge=1, le=3650)
    turnaround_minutes: int = Field(default=0, ge=0, le=10080)
    requirements: list[RequirementWrite] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def rules(self) -> "PolicyWrite":
        if self.max_nights is not None and self.max_nights < self.min_nights:
            raise ValueError("Maximum nights cannot be below minimum nights")
        if self.check_in_time.tzinfo or self.check_out_time.tzinfo:
            raise ValueError("Check-in/out times must be local wall times without offsets")
        if len({item.resource_type for item in self.requirements}) != len(self.requirements):
            raise ValueError("Resource requirement types must be unique")
        return self


class OfferingWrite(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    category: str = Field(default="general", min_length=1, max_length=80)
    booking_mode: Mode = "TIMED"
    default_duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    min_duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    max_duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    capacity_limit: int | None = Field(default=None, ge=1, le=1000000)
    active: bool = True
    policy: PolicyWrite = Field(default_factory=PolicyWrite)

    @model_validator(mode="after")
    def rules(self) -> "OfferingWrite":
        low, high, default = (
            self.min_duration_minutes,
            self.max_duration_minutes,
            self.default_duration_minutes,
        )
        if low and high and high < low:
            raise ValueError("Maximum duration cannot be below minimum duration")
        if default and ((low and default < low) or (high and default > high)):
            raise ValueError("Default duration must be within duration bounds")
        if self.booking_mode == "CAPACITY" and self.capacity_limit is None:
            raise ValueError("Capacity mode requires an offering capacity limit")
        return self


class ResourceWrite(Input):
    name: str = Field(min_length=1, max_length=200)
    resource_type: str = Field(min_length=1, max_length=80)
    capacity: int | None = Field(default=None, ge=1, le=1000000)
    allocation_mode: Literal["EXCLUSIVE", "SHARED"] = "EXCLUSIVE"
    active: bool = True

    @model_validator(mode="after")
    def rules(self) -> "ResourceWrite":
        if self.allocation_mode == "SHARED" and self.capacity is None:
            raise ValueError("Shared resources require a capacity")
        return self


class CompatibilityWrite(Input):
    offering_id: UUID
    resource_id: UUID


class CustomerWrite(Input):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)
    phone: str | None = Field(default=None, max_length=50)
    notification_consent: bool = False

    @field_validator("email", mode="after")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return value.lower()


class WindowInput(Input):
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    local_start_at: datetime | None = None
    local_end_at: datetime | None = None
    local_start_date: date | None = None
    local_end_date: date | None = None


class BookingInput(WindowInput):
    offering_id: UUID
    party_size: int | None = Field(default=None, ge=1, le=1000000)
    quantity: int = Field(default=1, ge=1, le=1000000)
    resource_ids: list[UUID] | None = Field(default=None, max_length=40)


class ReservationWrite(BookingInput):
    customer_id: UUID
    status: Status = "CONFIRMED"
    expected_version: int | None = Field(default=None, ge=1)


class RescheduleWrite(WindowInput):
    resource_ids: list[UUID] | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, ge=1)


class AssignWrite(Input):
    resource_ids: list[UUID] = Field(max_length=40)
    expected_version: int | None = Field(default=None, ge=1)


class StatusWrite(Input):
    status: Status
    expected_version: int | None = Field(default=None, ge=1)


class WaitlistWrite(WindowInput):
    customer_id: UUID
    offering_id: UUID
    available_from: AwareDatetime | None = None
    available_until: AwareDatetime | None = None
    preferred_resource_id: UUID | None = None
    party_size: int | None = Field(default=None, ge=1, le=1000000)
    quantity: int = Field(default=1, ge=1, le=1000000)
    status: Literal["ACTIVE", "INACTIVE", "FULFILLED"] = "ACTIVE"


class PeriodsInput(Input):
    offering_id: UUID
    start_at: AwareDatetime
    end_at: AwareDatetime
    duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    nights: int = Field(default=1, ge=1, le=3650)
    step_minutes: int = Field(default=30, ge=5, le=1440)
    party_size: int | None = Field(default=None, ge=1)
    quantity: int = Field(default=1, ge=1)


class BusinessUpdate(Input):
    business_type: str = Field(min_length=1, max_length=80)
    timezone: str = Field(max_length=100)

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone") from None
        return value


class Record(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID


class OfferingProfile(OfferingWrite, Record):
    created_at: datetime
    updated_at: datetime


class ResourceProfile(ResourceWrite, Record):
    created_at: datetime
    updated_at: datetime


class CustomerProfile(CustomerWrite, Record):
    created_at: datetime
    updated_at: datetime


class CompatibilityProfile(CompatibilityWrite, Record):
    created_at: datetime


class ReservationProfile(Record):
    customer_id: UUID
    offering_id: UUID
    start_at: datetime
    end_at: datetime
    local_start_date: date | None
    local_end_date: date | None
    party_size: int | None
    quantity: int
    status: str
    resource_ids: list[UUID]
    google_calendar_id: str | None
    google_event_id: str | None
    calendar_sync_status: str
    sync_version: int
    created_at: datetime
    updated_at: datetime


class WaitlistProfile(Record):
    customer_id: UUID
    offering_id: UUID
    available_from: datetime
    available_until: datetime
    local_start_date: date | None
    local_end_date: date | None
    preferred_resource_id: UUID | None
    party_size: int | None
    quantity: int
    status: str
    created_at: datetime
    updated_at: datetime


class AvailabilityProfile(BaseModel):
    available: bool
    reason: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    resources: list[ResourceProfile] = Field(default_factory=list)


class OutboxProfile(Record):
    reservation_id: UUID
    event_type: str
    status: str
    attempts: int
    last_error: str | None
    next_attempt_at: datetime
    processed_at: datetime | None
