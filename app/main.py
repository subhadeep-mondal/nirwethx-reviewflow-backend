from __future__ import annotations
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_, and_, inspect, text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
import csv, io, json, time, uuid, hmac, hashlib, httpx

from .config import settings
from .database import Base, engine, get_db, SessionLocal
from .models import Tenant, User, QRCode, ScanEvent, Feedback, Notification, Subscription, AuditLog, ContactTicket, ConsentRecord, now_utc
from .schemas import RegisterIn, LoginIn, QRCreateIn, QRUpdateIn, FeedbackIn, FeedbackUpdateIn, BusinessUpdateIn, ContactIn
from .security import hash_password, verify_password, create_token, current_user, slugify, hash_ip, new_id

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.environment.lower() == "production":
        if settings.jwt_secret == "development-only-change-this-secret" or len(settings.jwt_secret) < 32:
            raise RuntimeError("Set a strong JWT_SECRET (at least 32 characters) before production startup")
        if not settings.cookie_secure:
            raise RuntimeError("COOKIE_SECURE must be true in production")
    Base.metadata.create_all(bind=engine)
    # Add the users.phone column to existing databases without losing account data.
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "phone" not in user_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(24) NOT NULL DEFAULT ''"))
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "is_active" not in user_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
    feedback_columns = {column["name"] for column in inspect(engine).get_columns("feedback_entries")}
    if "category_ratings" not in feedback_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE feedback_entries ADD COLUMN category_ratings JSON NOT NULL DEFAULT '{}'"))
    tenant_columns = {column["name"] for column in inspect(engine).get_columns("tenants")}
    if "google_business_url" not in tenant_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tenants ADD COLUMN google_business_url TEXT NOT NULL DEFAULT ''"))

    # Promote only the explicitly configured existing account to platform admin.
    # ADMIN_EMAIL must be set by the deployment owner in the Render environment.
    if settings.admin_email.strip():
        with SessionLocal() as db:
            admin_user = db.query(User).filter(
                func.lower(User.email) == settings.admin_email.strip().lower()
            ).first()
            if admin_user and admin_user.role != "platform_admin":
                admin_user.role = "platform_admin"
                db.commit()
                print(f"Configured platform administrator: {admin_user.email}")
            elif not admin_user:
                print("ADMIN_EMAIL is configured, but no matching user exists yet.")

    yield

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def set_session_cookie(response: Response, token: str):
    response.set_cookie("rf_session", token, httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite, max_age=settings.jwt_expire_minutes * 60, path="/")

def audit(db: Session, user: User, action: str, resource_type: str, resource_id: str = "", metadata: dict | None = None):
    db.add(AuditLog(id=new_id(), tenant_id=user.tenant_id, actor_id=user.id, actor_email=user.email, action=action, resource_type=resource_type, resource_id=resource_id, metadata_json=metadata or {}))

def tenant_for(db: Session, user: User) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant: raise HTTPException(404, "Business profile not found")
    return tenant

def qr_dict(qr: QRCode):
    return {"id": qr.id, "label": qr.label, "placement_location": qr.placement_location, "short_code": qr.short_code, "theme": qr.theme, "call_to_action": qr.call_to_action, "scans_count": qr.scans_count, "google_clicks_count": qr.google_clicks_count, "feedback_count": qr.feedback_count, "is_active": qr.is_active, "created_at": qr.created_at}

def feedback_dict(db: Session, fb: Feedback):
    qr = db.query(QRCode).filter(QRCode.id == fb.qr_id, QRCode.tenant_id == fb.tenant_id).first()
    return {"id": fb.id, "qr_id": fb.qr_id, "qr_label": qr.label if qr else "Deleted QR", "rating": fb.rating, "comment": fb.comment, "category_ratings": fb.category_ratings or {}, "customer_name": fb.customer_name, "customer_phone": fb.customer_phone, "consent_to_contact": fb.consent_to_contact, "status": fb.status, "internal_note": fb.internal_note, "created_at": fb.created_at}

def subscription_dict(sub: Subscription | None):
    if not sub: return None
    return {"id": sub.id, "plan_tier": sub.plan_tier, "status": sub.status, "amount_inr": sub.amount_inr, "billing_cycle": sub.billing_cycle, "provider": sub.provider, "current_period_start": sub.current_period_start, "current_period_end": sub.current_period_end, "cancel_at_period_end": sub.cancel_at_period_end}

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "nirwethx-reviewflow-api", "environment": settings.environment}

# --- Authentication and onboarding ---
@app.post("/api/auth/register")
def register(payload: RegisterIn, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.query(User).filter(func.lower(User.email) == email).first(): raise HTTPException(409, "An account with this email already exists")
    tenant_id, user_id = new_id(), new_id()
    base = slugify(payload.business_name)
    slug = base
    while db.query(Tenant).filter(Tenant.slug == slug).first(): slug = f"{base}-{uuid.uuid4().hex[:5]}"
    if not payload.google_review_url.startswith("https://"): raise HTTPException(422, "Google review URL must use HTTPS")
    if payload.google_business_url and not payload.google_business_url.startswith("https://"): raise HTTPException(422, "Google Business URL must use HTTPS")
    tenant = Tenant(id=tenant_id, owner_user_id=user_id, slug=slug, business_name=payload.business_name.strip(), category=payload.category, city=payload.city, state=payload.state, phone=payload.phone, google_review_url=payload.google_review_url, google_business_url=payload.google_business_url, onboarding_completed=True)
    user = User(id=user_id, tenant_id=tenant_id, email=email, full_name=payload.full_name.strip(), phone=payload.phone.strip(), password_hash=hash_password(payload.password))
    qr = QRCode(id=new_id(), tenant_id=tenant_id, label="Primary Counter", placement_location="Main counter", short_code=uuid.uuid4().hex[:10], theme="teal")
    sub = Subscription(id=new_id(), tenant_id=tenant_id)
    try:
        # Persist the parent tenant before inserting rows that reference it.
        db.add(tenant)
        db.flush()
        db.add_all([user, qr, sub])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        print("REGISTRATION DATABASE ERROR:", str(exc.orig))
        raise HTTPException(
            status_code=409,
            detail="Registration failed due to a database constraint. Check server logs."
        )
    set_session_cookie(response, create_token(user))
    audit(db, user, "account.registered", "auth", user_id); db.commit()
    return {"user": {"id": user.id, "email": user.email, "full_name": user.full_name, "phone": user.phone}, "business": {"id": tenant.id, "slug": tenant.slug, "business_name": tenant.business_name}}

@app.post("/api/auth/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash): raise HTTPException(401, "Invalid email or password")
    if not user.is_active: raise HTTPException(403, "This account has been disabled. Contact support.")
    user.last_login_at = now_utc(); set_session_cookie(response, create_token(user)); db.commit()
    return {"user": {"id": user.id, "email": user.email, "full_name": user.full_name, "phone": user.phone}}

@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie("rf_session", path="/", secure=settings.cookie_secure, httponly=True, samesite=settings.cookie_samesite); return {"ok": True}

@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tenant = tenant_for(db, user)
    return {"user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "phone": user.phone, "is_active": user.is_active}, "business": {"id": tenant.id, "slug": tenant.slug, "business_name": tenant.business_name, "category": tenant.category, "city": tenant.city, "state": tenant.state, "phone": tenant.phone, "google_review_url": tenant.google_review_url, "google_business_url": tenant.google_business_url, "welcome_headline": tenant.welcome_headline, "welcome_subtext": tenant.welcome_subtext, "brand_accent": tenant.brand_accent, "show_direct_feedback_form": tenant.show_direct_feedback_form}}

# --- Dashboard and analytics ---
@app.get("/api/dashboard/summary")
def dashboard_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tenant_id = user.tenant_id
    qrs = db.query(QRCode).filter(QRCode.tenant_id == tenant_id).all()
    feedback = db.query(Feedback).filter(Feedback.tenant_id == tenant_id).all()
    scans = db.query(ScanEvent).filter(ScanEvent.tenant_id == tenant_id).all()
    ratings = [f.rating for f in feedback]
    average = round(sum(ratings) / len(ratings), 2) if ratings else 0
    unread = db.query(Notification).filter(Notification.tenant_id == tenant_id, Notification.is_read == False).count()
    recent = sorted(feedback, key=lambda x: x.created_at, reverse=True)[:6]
    return {"metrics": {"total_scans": sum(q.scans_count for q in qrs), "google_clicks": sum(q.google_clicks_count for q in qrs), "feedback_count": len(feedback), "average_rating": average, "active_qr_codes": sum(1 for q in qrs if q.is_active), "unread_notifications": unread, "new_feedback": sum(1 for f in feedback if f.status == "new")}, "recent_feedback": [feedback_dict(db, f) for f in recent], "qr_codes": [qr_dict(q) for q in qrs[:5]]}

@app.get("/api/analytics")
def analytics(days: int = Query(default=30, ge=7, le=90), user: User = Depends(current_user), db: Session = Depends(get_db)):
    since = now_utc() - timedelta(days=days - 1)
    events = db.query(ScanEvent).filter(ScanEvent.tenant_id == user.tenant_id, ScanEvent.created_at >= since).all()
    feedback = db.query(Feedback).filter(Feedback.tenant_id == user.tenant_id, Feedback.created_at >= since).all()
    by_day = defaultdict(lambda: {"scans": 0, "google_clicks": 0, "feedback": 0, "ratings": []})
    for e in events:
        key = e.created_at.date().isoformat()
        if e.event_type == "page_scan": by_day[key]["scans"] += 1
        elif e.event_type == "google_review_click": by_day[key]["google_clicks"] += 1
    for f in feedback: by_day[f.created_at.date().isoformat()]["feedback"] += 1; by_day[f.created_at.date().isoformat()]["ratings"].append(f.rating)
    trend = []
    for i in range(days):
        d = (now_utc().date() - timedelta(days=days - 1 - i)).isoformat(); row = by_day[d]
        trend.append({"date": d, "scans": row["scans"], "google_clicks": row["google_clicks"], "feedback": row["feedback"], "average_rating": round(sum(row["ratings"])/len(row["ratings"]), 2) if row["ratings"] else None})
    all_feedback = db.query(Feedback).filter(Feedback.tenant_id == user.tenant_id).all()
    dist = [{"stars": star, "count": sum(1 for f in all_feedback if f.rating == star)} for star in range(5, 0, -1)]
    total = len(all_feedback)
    for item in dist: item["percentage"] = round(item["count"] * 100 / total, 1) if total else 0
    return {"days": days, "trend": trend, "rating_distribution": dist}

# --- QR management ---
@app.get("/api/qrcodes")
def list_qrs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [qr_dict(q) for q in db.query(QRCode).filter(QRCode.tenant_id == user.tenant_id).order_by(desc(QRCode.created_at)).all()]

@app.post("/api/qrcodes")
def create_qr(payload: QRCreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    qr = QRCode(id=new_id(), tenant_id=user.tenant_id, label=payload.label.strip(), placement_location=payload.placement_location, theme=payload.theme, call_to_action=payload.call_to_action, short_code=uuid.uuid4().hex[:10])
    db.add(qr); audit(db, user, "qr.created", "qr_code", qr.id); db.commit(); db.refresh(qr)
    return qr_dict(qr)

@app.patch("/api/qrcodes/{qr_id}")
def update_qr(qr_id: str, payload: QRUpdateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    qr = db.query(QRCode).filter(QRCode.id == qr_id, QRCode.tenant_id == user.tenant_id).first()
    if not qr: raise HTTPException(404, "QR code not found")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(qr, key, value)
    audit(db, user, "qr.updated", "qr_code", qr.id); db.commit(); db.refresh(qr); return qr_dict(qr)

# --- Public customer review flow. Google review CTA is always visible, irrespective of rating. ---
@app.get("/api/public/qr/{short_code}")
def public_qr(short_code: str, request: Request, db: Session = Depends(get_db)):
    qr = db.query(QRCode).filter(QRCode.short_code == short_code, QRCode.is_active == True).first()
    if not qr: raise HTTPException(404, "This review link is unavailable")
    tenant = db.query(Tenant).filter(Tenant.id == qr.tenant_id).first()
    if not tenant: raise HTTPException(404, "Business not found")
    qr.scans_count += 1
    db.add(ScanEvent(id=new_id(), tenant_id=tenant.id, qr_id=qr.id, event_type="page_scan", user_agent_summary=request.headers.get("user-agent", "Browser")[:255], ip_hash=hash_ip(request.client.host if request.client else "unknown")))
    db.commit()
    return {"business_name": tenant.business_name, "category": tenant.category, "city": tenant.city, "welcome_headline": tenant.welcome_headline, "welcome_subtext": tenant.welcome_subtext, "brand_accent": tenant.brand_accent, "show_direct_feedback_form": tenant.show_direct_feedback_form, "google_review_url": tenant.google_review_url, "qr_label": qr.label, "call_to_action": qr.call_to_action}

@app.post("/api/public/qr/{short_code}/google-click")
def public_google_click(short_code: str, request: Request, db: Session = Depends(get_db)):
    qr = db.query(QRCode).filter(QRCode.short_code == short_code, QRCode.is_active == True).first()
    if not qr: raise HTTPException(404, "QR code not found")
    qr.google_clicks_count += 1
    db.add(ScanEvent(id=new_id(), tenant_id=qr.tenant_id, qr_id=qr.id, event_type="google_review_click", user_agent_summary=request.headers.get("user-agent", "Browser")[:255], ip_hash=hash_ip(request.client.host if request.client else "unknown")))
    db.commit(); return {"ok": True}

@app.post("/api/public/qr/{short_code}/feedback")
def public_feedback(short_code: str, payload: FeedbackIn, request: Request, db: Session = Depends(get_db)):
    qr = db.query(QRCode).filter(QRCode.short_code == short_code, QRCode.is_active == True).first()
    if not qr: raise HTTPException(404, "QR code not found")
    tenant = db.query(Tenant).filter(Tenant.id == qr.tenant_id).first()
    if not tenant or not tenant.show_direct_feedback_form: raise HTTPException(403, "Direct feedback is currently unavailable")
    fb = Feedback(id=new_id(), tenant_id=tenant.id, qr_id=qr.id, rating=payload.rating, comment=payload.comment.strip(), category_ratings=payload.category_ratings, customer_name=payload.customer_name.strip() or "Anonymous Customer", customer_phone=payload.customer_phone.strip(), consent_to_contact=payload.consent_to_contact)
    qr.feedback_count += 1
    db.add(fb)
    db.add(ScanEvent(id=new_id(), tenant_id=tenant.id, qr_id=qr.id, event_type="direct_feedback_submit", rating_selected=payload.rating, user_agent_summary=request.headers.get("user-agent", "Browser")[:255], ip_hash=hash_ip(request.client.host if request.client else "unknown")))
    db.flush()
    message = f"{fb.customer_name} left {fb.rating}/5 feedback"
    db.add(Notification(id=new_id(), tenant_id=tenant.id, feedback_id=fb.id, event_type="review:urgent_low_rating" if fb.rating <= 2 else "review:created", title="New customer feedback", message=message, rating=fb.rating, customer_name=fb.customer_name, qr_label=qr.label))
    consent_payload = f"{tenant.id}|{fb.id}|{payload.consent_to_contact}|{payload.customer_name}|{payload.customer_phone}|DPDP-2023-v2.1"
    checksum = hashlib.sha256(consent_payload.encode()).hexdigest()
    db.add(ConsentRecord(id=new_id(), tenant_id=tenant.id, feedback_id=fb.id, data_fiduciary=tenant.business_name, consent_to_contact=payload.consent_to_contact, ip_hash=hash_ip(request.client.host if request.client else "unknown"), checksum_sha256=checksum))
    db.commit(); return {"ok": True, "message": "Thank you for sharing your feedback."}

# --- Feedback inbox ---
@app.get("/api/feedback")
def list_feedback(status: str | None = None, q: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(Feedback).filter(Feedback.tenant_id == user.tenant_id)
    if status in {"new", "acknowledged", "resolved"}: query = query.filter(Feedback.status == status)
    if q: query = query.filter((Feedback.customer_name.ilike(f"%{q}%")) | (Feedback.comment.ilike(f"%{q}%")))
    return [feedback_dict(db, f) for f in query.order_by(desc(Feedback.created_at)).limit(250).all()]

@app.patch("/api/feedback/{feedback_id}")
def update_feedback(feedback_id: str, payload: FeedbackUpdateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    fb = db.query(Feedback).filter(Feedback.id == feedback_id, Feedback.tenant_id == user.tenant_id).first()
    if not fb: raise HTTPException(404, "Feedback not found")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(fb, key, value)
    audit(db, user, "feedback.updated", "feedback", fb.id); db.commit(); return feedback_dict(db, fb)

# --- Notifications: database-backed polling works across API workers ---
@app.get("/api/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(Notification.tenant_id == user.tenant_id).order_by(desc(Notification.created_at)).limit(50).all()
    return [{"id": n.id, "feedback_id": n.feedback_id, "event_type": n.event_type, "title": n.title, "message": n.message, "rating": n.rating, "customer_name": n.customer_name, "qr_label": n.qr_label, "is_read": n.is_read, "created_at": n.created_at} for n in rows]

@app.post("/api/notifications/read-all")
def read_all_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.tenant_id == user.tenant_id, Notification.is_read == False).update({"is_read": True}); db.commit(); return {"ok": True}

@app.get("/api/notifications/stream")
def notification_stream(user: User = Depends(current_user)):
    tenant_id = user.tenant_id
    def generate():
        last_created_at = None
        last_id = ""
        while True:
            db = SessionLocal()
            try:
                query = db.query(Notification).filter(Notification.tenant_id == tenant_id)
                if last_created_at:
                    query = query.filter(or_(Notification.created_at > last_created_at, and_(Notification.created_at == last_created_at, Notification.id > last_id)))
                rows = query.order_by(Notification.created_at.asc(), Notification.id.asc()).limit(25).all()
                for n in rows:
                    last_created_at = n.created_at
                    last_id = n.id
                    payload = {"id": n.id, "feedback_id": n.feedback_id, "title": n.title, "message": n.message, "rating": n.rating, "created_at": n.created_at.isoformat()}
                    yield f"event: notification\ndata: {json.dumps(payload)}\n\n"
            finally: db.close()
            yield ": keep-alive\n\n"
            time.sleep(4)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# --- Business settings ---
@app.put("/api/business")
def update_business(payload: BusinessUpdateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tenant = tenant_for(db, user)
    if not payload.google_review_url.startswith("https://"): raise HTTPException(422, "Google review URL must use HTTPS")
    if payload.google_business_url and not payload.google_business_url.startswith("https://"): raise HTTPException(422, "Google Business URL must use HTTPS")
    if not payload.brand_accent.startswith("#") or len(payload.brand_accent) not in (4, 7): raise HTTPException(422, "Brand accent must be a hex colour")
    for key, value in payload.model_dump().items(): setattr(tenant, key, value)
    tenant.updated_at = now_utc(); audit(db, user, "business.updated", "business_profile", tenant.id); db.commit()
    return {"ok": True}

# --- Billing ---
@app.get("/api/billing")
def billing(user: User = Depends(current_user), db: Session = Depends(get_db)):
    sub = db.query(Subscription).filter(Subscription.tenant_id == user.tenant_id).first()
    return {"subscription": subscription_dict(sub), "razorpay_configured": bool(settings.razorpay_key_id and settings.razorpay_key_secret), "plans": [{"id": "monthly_299", "name": "Monthly", "amount_inr": 349, "cycle": "monthly"}, {"id": "yearly_2999", "name": "Annual", "amount_inr": 3499, "cycle": "yearly"}]}

@app.post("/api/billing/create-order")
async def create_billing_order(payload: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    plan_tier = payload.get("plan_tier")
    plans = {"monthly_299": (349, "monthly"), "yearly_2999": (3499, "yearly")}
    plan = plans.get(plan_tier)
    if not plan: raise HTTPException(422, "Choose a valid plan")
    amount, cycle = plan
    coupon_code = str(payload.get("coupon_code") or "").strip().upper()
    discount_percent = 0
    if coupon_code:
        if plan_tier != "yearly_2999" or coupon_code != "NEWUSER10OFF":
            raise HTTPException(422, "This coupon is valid only for the annual plan.")
        discount_percent = 10
        amount = int(round(amount * (100 - discount_percent) / 100))
    if not settings.razorpay_key_id or not settings.razorpay_key_secret: raise HTTPException(503, "Razorpay is not configured. Add API credentials to backend/.env to enable checkout.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post("https://api.razorpay.com/v1/orders", auth=(settings.razorpay_key_id, settings.razorpay_key_secret), json={"amount": amount * 100, "currency": "INR", "receipt": f"rf_{uuid.uuid4().hex[:18]}", "notes": {"tenant_id": user.tenant_id, "plan_tier": plan_tier, "coupon_code": coupon_code, "discount_percent": discount_percent}})
    if response.status_code >= 300: raise HTTPException(502, "Payment provider could not create an order")
    order = response.json(); sub = db.query(Subscription).filter(Subscription.tenant_id == user.tenant_id).first()
    if not sub: sub = Subscription(id=new_id(), tenant_id=user.tenant_id); db.add(sub)
    sub.provider_order_id = order["id"]; sub.provider = "razorpay"; sub.amount_inr = amount; sub.plan_tier = plan_tier; sub.billing_cycle = cycle; db.commit()
    return {"order_id": order["id"], "amount": order["amount"], "currency": order["currency"], "key_id": settings.razorpay_key_id, "business_name": tenant_for(db, user).business_name, "email": user.email, "contact": tenant_for(db, user).phone, "discount_percent": discount_percent, "coupon_code": coupon_code, "original_amount_inr": plan[0]}

@app.post("/api/billing/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body(); signature = request.headers.get("X-Razorpay-Signature", "")
    if not settings.razorpay_webhook_secret: raise HTTPException(503, "Webhook secret is not configured")
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature): raise HTTPException(401, "Invalid webhook signature")
    event = json.loads(raw or b"{}")
    if event.get("event") == "payment.captured":
        entity = event.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = entity.get("order_id"); payment_id = entity.get("id")
        sub = db.query(Subscription).filter(Subscription.provider_order_id == order_id).first()
        if sub and entity.get("currency") == "INR" and int(entity.get("amount") or 0) == sub.amount_inr * 100:
            sub.status = "active"; sub.provider_last_payment_id = payment_id or ""; sub.current_period_start = now_utc(); sub.current_period_end = now_utc() + (timedelta(days=365) if sub.billing_cycle == "yearly" else timedelta(days=30)); sub.updated_at = now_utc()
    elif event.get("event") == "payment.failed":
        entity = event.get("payload", {}).get("payment", {}).get("entity", {}); sub = db.query(Subscription).filter(Subscription.provider_order_id == entity.get("order_id")).first()
        if sub: sub.status = "past_due"
    db.commit(); return {"received": True}

# --- CSV export and contact ---
@app.get("/api/export/feedback.csv")
def export_feedback(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Feedback).filter(Feedback.tenant_id == user.tenant_id).order_by(desc(Feedback.created_at)).all()
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(["Created at", "Customer", "Phone", "Rating", "Comment", "Status", "QR", "Consent to contact"])
    for fb in rows:
        qr = db.query(QRCode).filter(QRCode.id == fb.qr_id).first(); writer.writerow([fb.created_at.isoformat(), fb.customer_name, fb.customer_phone, fb.rating, fb.comment, fb.status, qr.label if qr else "", fb.consent_to_contact])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reviewflow-feedback.csv"})

@app.post("/api/contact")
def create_contact(payload: ContactIn, db: Session = Depends(get_db)):
    ticket = ContactTicket(id=new_id(), ticket_type=payload.ticket_type, full_name=payload.full_name, email=payload.email.lower(), phone=payload.phone, business_name=payload.business_name, message=payload.message)
    db.add(ticket); db.commit(); return {"ok": True, "ticket_id": ticket.id, "message": "Your request has been received."}

# --- Platform super-admin ---
def require_platform_admin(user: User = Depends(current_user)) -> User:
    if user.role != "platform_admin":
        raise HTTPException(403, "Platform administrator access required")
    return user

@app.get("/api/admin/accounts")
def admin_accounts(admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    users = db.query(User).filter(User.role != "platform_admin").order_by(desc(User.created_at)).all()
    results = []
    revenue_total = 0
    revenue_30_days = 0
    revenue_today = 0
    india_tz = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(india_tz).date()
    thirty_days_ago = today - timedelta(days=29)
    for account in users:
        tenant = db.query(Tenant).filter(Tenant.id == account.tenant_id).first()
        if not tenant: continue
        sub = db.query(Subscription).filter(Subscription.tenant_id == tenant.id).first()
        reviews = db.query(func.count(Feedback.id)).filter(Feedback.tenant_id == tenant.id).scalar() or 0
        plan_amount = int(sub.amount_inr or 0) if sub else 0
        joined_at = account.created_at
        if joined_at:
            joined_local_date = joined_at.replace(tzinfo=timezone.utc).astimezone(india_tz).date() if joined_at.tzinfo is None else joined_at.astimezone(india_tz).date()
            revenue_total += plan_amount
            if thirty_days_ago <= joined_local_date <= today: revenue_30_days += plan_amount
            if joined_local_date == today: revenue_today += plan_amount
        results.append({
            "user_id": account.id, "email": account.email, "full_name": account.full_name,
            "phone": account.phone, "is_active": account.is_active, "role": account.role,
            "business_name": tenant.business_name, "category": tenant.category,
            "plan": sub.plan_tier if sub else "none", "plan_amount_inr": plan_amount,
            "plan_status": sub.status if sub else "none",
            "plan_end_date": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
            "review_count": reviews, "joined_at": joined_at.isoformat() if joined_at else None
        })
    return {"accounts": results, "total": len(results), "revenue": {"currency": "INR", "basis": "signup_plan_amount", "total_to_date": revenue_total, "last_30_days": revenue_30_days, "today": revenue_today}}
@app.patch("/api/admin/accounts/{user_id}/status")
def admin_set_account_status(user_id: str, payload: dict, admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    active = payload.get("is_active")
    if not isinstance(active, bool): raise HTTPException(422, "is_active must be true or false")
    account = db.query(User).filter(User.id == user_id).first()
    if not account: raise HTTPException(404, "Account not found")
    if account.id == admin.id and not active: raise HTTPException(400, "You cannot disable your own administrator account")
    if account.role == "platform_admin": raise HTTPException(403, "Platform administrator accounts cannot be disabled here")
    account.is_active = active
    db.add(AuditLog(id=new_id(), tenant_id=account.tenant_id, actor_id=admin.id, actor_email=admin.email, action="account.enabled" if active else "account.disabled", resource_type="user", resource_id=account.id, metadata_json={"target_email": account.email}))
    db.commit()
    return {"ok": True, "user_id": account.id, "is_active": account.is_active}
