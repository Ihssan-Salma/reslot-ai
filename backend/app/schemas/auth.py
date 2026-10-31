from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class LoginCredentials(Credentials):
    password: str = Field(min_length=1, max_length=128)


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    created_at: datetime
    updated_at: datetime
    active_tenant_id: UUID | None = None


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    business_type: str = Field(default="other", min_length=1, max_length=80)
    timezone: str = Field(default="Europe/Paris", max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone") from None
        return value


class TenantProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    business_type: str
    timezone: str
    status: str
    created_at: datetime
    role: Literal["owner", "admin", "staff"]


class TenantSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
