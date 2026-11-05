from datetime import datetime, timezone, timedelta
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, Float, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def now_utc(): return datetime.now(timezone.utc)

class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="Other")
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str] = mapped_column(String(24), default="")
    gstin: Mapped[str] = mapped_column(String(30), default="")
    google_review_url: Mapped[str] = mapped_column(Text, default="https://www.google.com/maps")
    google_business_url: Mapped[str] = mapped_column(Text, default="")
    welcome_headline: Mapped[str] = mapped_column(String(180), default="Share your honest experience with us")
    welcome_subtext: Mapped[str] = mapped_column(Text, default="Your feedback helps us serve you better.")
    brand_accent: Mapped[str] = mapped_column(String(20), default="#14b8a6")
    show_direct_feedback_form: Mapped[bool] = mapped_column(Boolean, default=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class QRCode(Base):
    __tablename__ = "qr_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    placement_location: Mapped[str] = mapped_column(String(120), default="")
    short_code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    theme: Mapped[str] = mapped_column(String(20), default="teal")
    call_to_action: Mapped[str] = mapped_column(String(140), default="Share your experience")
    scans_count: Mapped[int] = mapped_column(Integer, default=0)
    google_clicks_count: Mapped[int] = mapped_column(Integer, default=0)
    feedback_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ScanEvent(Base):
    __tablename__ = "scan_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    qr_id: Mapped[str] = mapped_column(ForeignKey("qr_codes.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    rating_selected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_agent_summary: Mapped[str] = mapped_column(String(255), default="Browser")
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class Feedback(Base):
    __tablename__ = "feedback_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    qr_id: Mapped[str] = mapped_column(ForeignKey("qr_codes.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    category_ratings: Mapped[dict] = mapped_column(JSON, default=dict)
    customer_name: Mapped[str] = mapped_column(String(120), default="Anonymous Customer")
    customer_phone: Mapped[str] = mapped_column(String(24), default="")
    consent_to_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    internal_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class Notification(Base):
    __tablename__ = "review_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback_entries.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(140))
    message: Mapped[str] = mapped_column(Text)
    rating: Mapped[int] = mapped_column(Integer)
    customer_name: Mapped[str] = mapped_column(String(120))
    qr_label: Mapped[str] = mapped_column(String(100), default="QR code")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, index=True)
    plan_tier: Mapped[str] = mapped_column(String(30), default="free_trial")
    status: Mapped[str] = mapped_column(String(24), default="trialing")
    amount_inr: Mapped[int] = mapped_column(Integer, default=0)
    amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(20), default="trial")
    provider: Mapped[str] = mapped_column(String(20), default="test_mode")
    provider_order_id: Mapped[str] = mapped_column(String(120), default="")
    provider_last_payment_id: Mapped[str] = mapped_column(String(120), default="")
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: now_utc() + timedelta(days=14))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    actor_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str] = mapped_column(String(100), default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContactTicket(Base):
    __tablename__ = "contact_tickets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_type: Mapped[str] = mapped_column(String(40), default="general_inquiry")
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(24), default="")
    business_name: Mapped[str] = mapped_column(String(120), default="")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ConsentRecord(Base):
    __tablename__ = "dpdp_consent_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    feedback_id: Mapped[str] = mapped_column(ForeignKey("feedback_entries.id", ondelete="CASCADE"), index=True)
    consent_version: Mapped[str] = mapped_column(String(40), default="DPDP-2023-v2.1")
    notice_provided: Mapped[bool] = mapped_column(Boolean, default=True)
    purpose: Mapped[str] = mapped_column(String(100), default="customer_feedback_and_service_resolution")
    data_fiduciary: Mapped[str] = mapped_column(String(120))
    consent_to_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: now_utc() + timedelta(days=365))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
