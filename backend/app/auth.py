"""Accounts: scrypt password hashing, short-lived JWTs, and the FastAPI dependencies that use them."""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import time

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.observability import client_ip, limiter

log = logging.getLogger("rag.auth")

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}  # viewers ask; editors also upload; admins manage everything
_ephemeral_secret = secrets.token_hex(32)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise ValueError("Enter a valid email address")
    return email


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")
    if len(password) > 256:
        raise ValueError("Password is too long")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# Used to spend the same time on unknown emails as on wrong passwords, so login does not reveal which exist.
_DUMMY_HASH = hash_password("not-a-real-password")


def _secret() -> str:
    s = get_settings().jwt_secret
    if s:
        return s
    log.warning("jwt_secret_ephemeral", extra={"hint": "set JWT_SECRET so sessions survive restarts"})
    return _ephemeral_secret


def create_token(user: User) -> str:
    now = int(time.time())
    payload = {"sub": str(user.id), "iat": now, "exp": now + get_settings().jwt_ttl_minutes * 60}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def _user_from_token(token: str, db: Session) -> User | None:
    try:
        claims = jwt.decode(token, _secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
        return db.get(User, int(claims["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def optional_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User | None:
    """The signed-in user, or None for anonymous / invalid credentials (never raises)."""
    if not get_settings().auth_enabled:
        return None
    scheme, _, token = authorization.partition(" ")
    return _user_from_token(token, db) if scheme.lower() == "bearer" and token else None


def get_current_user(user: User | None = Depends(optional_user)) -> User | None:
    """The signed-in user. None means anonymous, which is only allowed while auth is disabled."""
    if user is None and get_settings().auth_enabled:
        raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
    return user


def auth_rate_limit(request: Request) -> None:
    limit = get_settings().auth_rate_limit_per_minute
    if limit <= 0:
        return
    retry = limiter.check(f"auth:{client_ip(request)}", limit)
    if retry:
        raise HTTPException(429, "Too many attempts. Please wait a moment.", headers={"Retry-After": str(int(retry) + 1)})


def require_role(minimum: str):
    """Dependency: the signed-in user must have at least this role. No-op while auth is disabled."""

    def check(user: User | None = Depends(get_current_user)) -> User | None:
        if user is not None and ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum]:
            raise HTTPException(403, f"This needs the {minimum} role")
        return user

    return check


def admin_access(user: User | None = Depends(optional_user), x_admin_key: str = Header(default="")) -> User | None:
    """Admin endpoints accept either an admin account or the ADMIN_API_KEY header (used by scripts)."""
    expected = get_settings().admin_api_key
    if expected and x_admin_key and secrets.compare_digest(x_admin_key, expected):
        return user
    if user is not None and user.role == "admin":
        return user
    if not expected and not get_settings().auth_enabled:  # nothing configured: the admin surface does not exist
        raise HTTPException(404, "Not found")
    raise HTTPException(401 if user is None else 403, "Admin access required")


def register_user(db: Session, email: str, password: str) -> User:
    email = normalize_email(email)
    validate_password(password)
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("An account with this email already exists")
    first = db.scalar(select(func.count()).select_from(User)) == 0  # whoever sets the system up administers it
    default = get_settings().default_role if get_settings().default_role in ROLE_RANK else "editor"
    user = User(email=email, password_hash=hash_password(password), role="admin" if first else default)
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if user and ok else None
