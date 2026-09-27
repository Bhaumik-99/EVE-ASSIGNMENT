from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import BookingStatus, PaymentStatus, UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)

    @field_validator("full_name")
    @classmethod
    def full_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("full_name cannot be blank")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    full_name: str
    role: UserRole


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TestCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)

    @field_validator("name")
    @classmethod
    def test_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class TestUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)

    @field_validator("name")
    @classmethod
    def update_name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class TestOut(TestCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    centre_id: str


class CentreCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    location: str = Field(min_length=2, max_length=255)

    @field_validator("name", "location")
    @classmethod
    def fields_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class CentreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    location: str | None = Field(default=None, min_length=2, max_length=255)

    @field_validator("name", "location")
    @classmethod
    def update_fields_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class CentreOut(CentreCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tests: list[TestOut] = Field(default_factory=list)


class BookingCreate(BaseModel):
    test_id: str
    appointment_at: datetime

    @field_validator("appointment_at")
    @classmethod
    def appointment_must_be_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("appointment_at must include timezone information")
        normalized = value.astimezone(timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("appointment_at must be in the future")
        return normalized


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    test_id: str
    centre_id: str
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus


class PaymentRequest(BaseModel):
    booking_id: str
    force_status: PaymentStatus | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    booking_id: str
    provider_payment_id: str
    status: PaymentStatus
    amount: Decimal


class PaymentWebhook(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    payment_id: str = Field(min_length=1, max_length=100)
    booking_id: str
    status: PaymentStatus
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class HealthOut(BaseModel):
    status: str
