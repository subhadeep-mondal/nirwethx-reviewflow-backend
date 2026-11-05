from pydantic import BaseModel, EmailStr, Field, HttpUrl, ConfigDict
from typing import Literal

class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    business_name: str = Field(min_length=2, max_length=120)
    category: str = "Other"
    city: str = ""
    state: str = ""
    phone: str = ""
    google_review_url: str = "https://www.google.com/maps"
    google_business_url: str = ""

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class QRCreateIn(BaseModel):
    label: str = Field(min_length=2, max_length=100)
    placement_location: str = ""
    theme: Literal["charcoal", "teal", "light"] = "teal"
    call_to_action: str = "Share your experience"

class QRUpdateIn(BaseModel):
    label: str | None = None
    placement_location: str | None = None
    is_active: bool | None = None
    theme: Literal["charcoal", "teal", "light"] | None = None

class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)
    customer_name: str = Field(default="Anonymous Customer", max_length=120)
    customer_phone: str = Field(default="", max_length=24)
    consent_to_contact: bool = False

class FeedbackUpdateIn(BaseModel):
    status: Literal["new", "acknowledged", "resolved"] | None = None
    internal_note: str | None = Field(default=None, max_length=2000)

class BusinessUpdateIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    category: str = "Other"
    city: str = ""
    state: str = ""
    phone: str = ""
    gstin: str = ""
    google_review_url: str
    google_business_url: str = ""
    welcome_headline: str = Field(max_length=180)
    welcome_subtext: str = Field(max_length=1000)
    brand_accent: str = "#14b8a6"
    show_direct_feedback_form: bool = True

class ContactIn(BaseModel):
    ticket_type: str = "general_inquiry"
    full_name: str
    email: EmailStr
    phone: str = ""
    business_name: str = ""
    message: str = Field(min_length=10, max_length=4000)

class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
