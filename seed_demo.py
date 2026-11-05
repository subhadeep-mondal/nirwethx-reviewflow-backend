"""Create a disposable demo workspace. Run manually: python seed_demo.py"""
from datetime import timedelta
from app.database import Base, engine, SessionLocal
from app.models import Tenant, User, QRCode, Feedback, ScanEvent, Notification, Subscription, now_utc
from app.security import hash_password, new_id

Base.metadata.create_all(bind=engine)
db = SessionLocal()
try:
    email = "demo@nirwethx.com"
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        print("Demo account already exists. No changes made.")
    else:
        uid, tid = new_id(), new_id()
        tenant = Tenant(id=tid, owner_user_id=uid, slug="demo-brew-house", business_name="Demo Brew House", category="Cafe & Restaurant", city="Kolkata", state="West Bengal", phone="+91 98765 43210", google_review_url="https://www.google.com/maps", onboarding_completed=True)
        user = User(id=uid, tenant_id=tid, email=email, full_name="Demo Owner", password_hash=hash_password("DemoPass123!"))
        qr = QRCode(id=new_id(), tenant_id=tid, label="Main counter", placement_location="Billing counter", short_code="demobrew01", theme="teal", scans_count=128, google_clicks_count=41, feedback_count=12)
        qr2 = QRCode(id=new_id(), tenant_id=tid, label="Dining tables", placement_location="Tables 1–6", short_code="demobrew02", theme="charcoal", scans_count=76, google_clicks_count=28, feedback_count=8)
        db.add_all([tenant,user,qr,qr2,Subscription(id=new_id(),tenant_id=tid)])
        db.flush()
        sample = [(5,"Loved the coffee and the friendly service.","Aarav Mehta","resolved",5),(4,"Great ambience. Will visit again.","Priya S.","acknowledged",4),(2,"The wait was longer than expected.","Rahul D.","new",2),(5,"Excellent experience from start to finish.","Mira Roy","new",5)]
        for days, comment, name, status, rating in sample:
            created = now_utc() - timedelta(days=days)
            fb = Feedback(id=new_id(),tenant_id=tid,qr_id=qr.id,rating=rating,comment=comment,customer_name=name,status=status,created_at=created)
            db.add(fb); db.flush()
            db.add(Notification(id=new_id(),tenant_id=tid,feedback_id=fb.id,event_type="review:urgent_low_rating" if rating<=2 else "review:created",title="New customer feedback",message=f"{name} left {rating}/5 feedback",rating=rating,customer_name=name,qr_label=qr.label,created_at=created))
        for days in range(1,15):
            created=now_utc()-timedelta(days=days)
            db.add(ScanEvent(id=new_id(),tenant_id=tid,qr_id=qr.id,event_type="page_scan",created_at=created,ip_hash="demo"))
            if days%2==0: db.add(ScanEvent(id=new_id(),tenant_id=tid,qr_id=qr.id,event_type="google_review_click",created_at=created,ip_hash="demo"))
        db.commit()
        print("Demo workspace created.")
        print("Email: demo@nirwethx.com")
        print("Password: DemoPass123!")
        print("Public review URL: http://localhost:5173/r/demobrew01")
finally:
    db.close()
