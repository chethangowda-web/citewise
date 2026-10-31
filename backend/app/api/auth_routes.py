import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE,
    auth_rate_limit,
    authenticate,
    check_not_locked,
    clear_failures,
    create_reset_token,
    create_token,
    get_current_user,
    record_failure,
    register_user,
    reset_password,
    set_session_cookie,
    validate_password,
    verify_password,
    wants_cookie,
    hash_password,
)
from app.config import get_settings
from app.db import get_db
from app.mailer import send_mail
from app.models import AuditLog, Document, Escalation, User
from app.observability import limiter

router = APIRouter(prefix="/auth")
log = logging.getLogger("rag.auth")

RESET_REPLY = {"ok": True, "message": "If an account exists for that address, a reset link is on its way."}


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class EmailOnly(BaseModel):
    email: str = Field(max_length=254)


class ResetBody(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=256)


class DeleteAccount(BaseModel):
    password: str = Field(max_length=256)


class ChangePassword(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def user_info(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "role": user.role,
        "teams": [{"id": t.id, "name": t.name} for t in sorted(user.teams, key=lambda t: t.name)],
    }


def _session(user: User, request: Request, response: Response) -> dict:
    """Web app (custom header): an HttpOnly cookie and no token in the body, so scripts never see it.
    Everything else (scripts, tests, other clients): the token in the body, to send as a Bearer header."""
    token = create_token(user)
    if wants_cookie(request):
        set_session_cookie(response, request, token)
        return {"user": user_info(user)}
    return {"token": token, "user": user_info(user)}


def _require_enabled() -> None:
    if not get_settings().auth_enabled:
        raise HTTPException(404, "Accounts are not enabled")


@router.get("/config")
def auth_config():
    s = get_settings()
    return {"auth_enabled": s.auth_enabled, "allow_signup": s.allow_signup}


@router.post("/register", dependencies=[Depends(auth_rate_limit)])
def register(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    _require_enabled()
    if not s.allow_signup:
        raise HTTPException(403, "Sign-up is closed")
    try:
        user = register_user(db, body.email, body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _session(user, request, response)


@router.post("/login", dependencies=[Depends(auth_rate_limit)])
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    _require_enabled()
    email = body.email.strip().lower()
    check_not_locked(db, email)  # same answer whether or not the account exists
    user = authenticate(db, email, body.password)
    if user is None:
        record_failure(db, email)
        raise HTTPException(401, "Incorrect email or password")
    clear_failures(db, email)
    return _session(user, request, response)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/logout-all")
def logout_everywhere(response: Response, user: User | None = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revoke every session of this user, on every device, including this one."""
    if user is None:
        raise HTTPException(404, "Accounts are not enabled")
    user.token_version += 1
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password", dependencies=[Depends(auth_rate_limit)])
def change_password(
    body: ChangePassword,
    request: Request,
    response: Response,
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        raise HTTPException(404, "Accounts are not enabled")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Your current password is not right")
    try:
        validate_password(body.new_password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # every other device is signed out; this one gets a fresh token below
    db.commit()
    return _session(user, request, response)


@router.post("/forgot", dependencies=[Depends(auth_rate_limit)])
def forgot_password(body: EmailOnly, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Always answers the same, so it cannot be used to find out which addresses have accounts."""
    _require_enabled()
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user and not limiter.check(f"forgot:{email}", 3, window=3600):  # at most 3 emails an hour per address
        token = create_reset_token(db, user)
        link = f"{get_settings().public_url.rstrip('/')}/?reset={token}"
        minutes = get_settings().reset_ttl_minutes
        background.add_task(
            send_mail, user.email, "Reset your Citewise password",
            f"Someone asked to reset the password for this account.\n\nChoose a new password here "
            f"(the link works once and expires in {minutes} minutes):\n{link}\n\nIf it was not you, ignore this email.",
        )
    return RESET_REPLY


@router.post("/reset", dependencies=[Depends(auth_rate_limit)])
def reset(body: ResetBody, db: Session = Depends(get_db)):
    _require_enabled()
    try:
        reset_password(db, body.token, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/me/export")
def export_my_data(user: User | None = Depends(get_current_user)):
    """Everything the system holds about you, as one JSON file."""
    if user is None:
        raise HTTPException(404, "Accounts are not enabled")
    db = Session.object_session(user)
    data = {
        "account": {**user_info(user), "created_at": user.created_at.isoformat()},
        "conversations": [
            {
                "id": c.id, "title": c.title, "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat(),
                "messages": [
                    {
                        "role": m.role, "content": m.content, "created_at": m.created_at.isoformat(), "meta": m.meta,
                        "feedback": m.feedback, "feedback_reason": m.feedback_reason,
                    }
                    for m in c.messages
                ],
            }
            for c in sorted(user.sessions, key=lambda c: c.created_at)
        ],
        "questions_sent_to_staff": [
            {"question": e.question, "note": e.note, "status": e.status, "reply": e.reply, "created_at": e.created_at.isoformat()}
            for e in db.scalars(select(Escalation).where(Escalation.user_id == user.id).order_by(Escalation.id))
        ],
        "documents_you_uploaded": [
            {"title": d.title, "source": d.source, "version": d.version, "status": d.status, "filed": d.created_at.isoformat()}
            for d in db.scalars(select(Document).where(Document.owner_id == user.id).order_by(Document.id))
        ],
    }
    return Response(
        content=json.dumps(data, indent=2), media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="citewise-my-data.json"'},
    )


@router.delete("/me", dependencies=[Depends(auth_rate_limit)])
def delete_my_account(
    body: DeleteAccount, response: Response, user: User | None = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Erase the account: its conversations, private documents, memberships and (by default) audit rows."""
    if user is None:
        raise HTTPException(404, "Accounts are not enabled")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "That password is not right")
    if user.role == "admin" and (db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0) <= 1:
        raise HTTPException(409, "You are the only admin. Make someone else an admin first.")
    # a team's documents belong to the team, not to whoever uploaded them
    db.execute(update(Document).where(Document.owner_id == user.id, Document.team_id.is_not(None)).values(owner_id=None))
    if not get_settings().keep_audit_on_account_delete:
        db.execute(delete(AuditLog).where(AuditLog.user_id == user.id))
    db.delete(user)
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User | None = Depends(get_current_user)):
    return user_info(user) if user else None
