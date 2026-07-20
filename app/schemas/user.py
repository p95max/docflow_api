from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.timezones import validate_iana_timezone


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    timezone: str

    model_config = ConfigDict(from_attributes=True)


class UserTimezoneUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str) -> str:
        return validate_iana_timezone(value)
