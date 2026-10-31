"""What users say about answers: a thumbs up or down, and asking a person when the assistant cannot help."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import _uid, require_api_key
from app.auth import get_current_user
from app.db import get_db
from app.guardrails.input import redact_pii
from app.models import ChatMessage, Escalation, User
from app.observability import rate_limit

router = APIRouter(dependencies=[Depends(require_api_key), Depends(rate_limit)])


class FeedbackBody(BaseModel):
    rating: str = Field(pattern="^(up|down|none)$")
    reason: str | None = Field(default=None, max_length=500)


class EscalationBody(BaseModel):
    message_id: int
    note: str = Field(default="", max_length=1000)


def _own_answer(db: Session, message_id: int, user: User | None) -> ChatMessage:
    m = db.get(ChatMessage, message_id)
    if m is None or m.role != "assistant" or m.session.user_id != _uid(user):  # someone else's looks missing too
        raise HTTPException(404, "Answer not found")
    return m


def question_for(m: ChatMessage) -> str:
    """The question an answer responds to: the message just before it in the conversation."""
    msgs = m.session.messages
    i = next(k for k, x in enumerate(msgs) if x.id == m.id)
    return msgs[i - 1].content if i > 0 else ""


@router.post("/messages/{message_id}/feedback")
def give_feedback(
    message_id: int, body: FeedbackBody, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)
):
    m = _own_answer(db, message_id, user)
    if body.rating == "none":
        m.feedback, m.feedback_reason = None, None
    elif body.rating == "up":
        m.feedback, m.feedback_reason = 1, None
    else:
        reason = redact_pii((body.reason or "").strip())[0]  # people paste emails and numbers into free text
        m.feedback, m.feedback_reason = -1, reason or None
    db.commit()
    return {"message_id": m.id, "rating": body.rating}


def escalation_out(e: Escalation) -> dict:
    return {
        "id": e.id, "question": e.question, "note": e.note, "status": e.status, "reply": e.reply,
        "created_at": e.created_at.isoformat(), "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


@router.post("/escalations", status_code=201)
def ask_a_person(body: EscalationBody, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    m = _own_answer(db, body.message_id, user)
    question = question_for(m)
    existing = db.scalar(
        select(Escalation).where(
            Escalation.user_id == _uid(user), Escalation.session_id == m.session_id,
            Escalation.question == question, Escalation.status == "open",
        )
    )
    if existing:  # pressing the button twice does not queue the same question twice
        return {**escalation_out(existing), "already_sent": True}
    e = Escalation(
        user_id=_uid(user), session_id=m.session_id, question=question, note=redact_pii(body.note.strip())[0]
    )
    db.add(e)
    db.commit()
    return {**escalation_out(e), "already_sent": False}


@router.get("/escalations")
def my_escalations(db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    uid = _uid(user)
    owner = Escalation.user_id.is_(None) if uid is None else Escalation.user_id == uid
    return [escalation_out(e) for e in db.scalars(select(Escalation).where(owner).order_by(Escalation.id.desc()).limit(50))]
