from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import pytest

from app.database import Base, get_db
from app.main import app

@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    def override_get_db():
        db = TestingSession()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def register(client, email, business):
    response = client.post("/api/auth/register", json={
        "full_name": "Test Owner", "email": email, "password": "A-Strong-Test-Password-2026",
        "business_name": business, "category": "Cafe & Restaurant", "city": "Kolkata",
        "state": "West Bengal", "google_review_url": "https://www.google.com/maps",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_register_public_feedback_dashboard_and_export(client):
    register(client, "owner-one@example.com", "First Coffee")
    qr = client.get("/api/qrcodes").json()[0]
    public_page = client.get(f"/api/public/qr/{qr['short_code']}")
    assert public_page.status_code == 200
    assert public_page.json()["business_name"] == "First Coffee"
    feedback = client.post(f"/api/public/qr/{qr['short_code']}/feedback", json={
        "rating": 2, "comment": "Could be faster", "customer_name": "Customer", "consent_to_contact": False,
    })
    assert feedback.status_code == 200
    summary = client.get("/api/dashboard/summary").json()["metrics"]
    assert summary["feedback_count"] == 1
    assert summary["average_rating"] == 2.0
    assert summary["unread_notifications"] == 1
    assert len(client.get("/api/notifications").json()) == 1
    assert "Could be faster" in client.get("/api/export/feedback.csv").text


def test_tenant_data_isolation(client):
    register(client, "owner-one@example.com", "First Coffee")
    first_qr = client.get("/api/qrcodes").json()[0]
    client.post("/api/auth/logout")
    register(client, "owner-two@example.com", "Second Clinic")
    second_qrs = client.get("/api/qrcodes").json()
    assert len(second_qrs) == 1
    assert second_qrs[0]["id"] != first_qr["id"]
    assert client.get("/api/feedback").json() == []
    forbidden = client.patch(f"/api/qrcodes/{first_qr['id']}", json={"is_active": False})
    assert forbidden.status_code == 404


def test_auth_required_for_dashboard(client):
    response = client.get("/api/dashboard/summary")
    assert response.status_code == 401
