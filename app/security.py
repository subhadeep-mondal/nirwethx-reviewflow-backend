from datetime import datetime, timedelta, timezone
import hashlib, hmac, uuid, re
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import User

password_hash = PasswordHasher()

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try: return password_hash.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, ValueError): return False

def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("rf_session")
    if not token:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    if not token: raise HTTPException(401, "Authentication required")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user = db.query(User).filter(User.id == payload.get("sub"), User.tenant_id == payload.get("tenant_id")).first()
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired session")
    if not user: raise HTTPException(401, "Session user not found")
    return user

def slugify(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result[:80] or "business"

def hash_ip(ip: str) -> str:
    return hmac.new(settings.ip_hash_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()

def new_id() -> str: return str(uuid.uuid4())
