from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import auth_rate_limit, authenticate, create_token, get_current_user, register_user
from app.config import get_settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth")


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


def _session(user: User) -> dict:
    return {"token": create_token(user), "user": {"id": user.id, "email": user.email}}


@router.get("/config")
def auth_config():
    s = get_settings()
    return {"auth_enabled": s.auth_enabled, "allow_signup": s.allow_signup}


@router.post("/register", dependencies=[Depends(auth_rate_limit)])
def register(body: Credentials, db: Session = Depends(get_db)):
    s = get_settings()
    if not s.auth_enabled:
        raise HTTPException(404, "Accounts are not enabled")
    if not s.allow_signup:
        raise HTTPException(403, "Sign-up is closed")
    try:
        user = register_user(db, body.email, body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _session(user)


@router.post("/login", dependencies=[Depends(auth_rate_limit)])
def login(body: Credentials, db: Session = Depends(get_db)):
    if not get_settings().auth_enabled:
        raise HTTPException(404, "Accounts are not enabled")
    user = authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Incorrect email or password")
    return _session(user)


@router.get("/me")
def me(user: User | None = Depends(get_current_user)):
    return {"id": user.id, "email": user.email} if user else None
