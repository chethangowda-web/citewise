"""Admin tools around what users do: feedback review, the staff inbox, CSV export of the audit ledger, retention."""
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.feedback import escalation_out, question_for
from app.auth import admin_access
from app.db import get_db
from app.mailer import send_mail
from app.models import AuditLog, ChatMessage, Escalation, User
from app.retention import purge_expired

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_access)])


@router.get("/feedback")
def feedback_review(
    rating: str = Query("down", pattern="^(down|up|all)$"), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)
):
    """Answers people rated, newest first, with the question and the evidence, so bad ones can be fixed."""
    counts = dict(db.execute(select(ChatMessage.feedback, func.count()).where(ChatMessage.feedback.is_not(None)).group_by(ChatMessage.feedback)).all())
    q = select(ChatMessage).where(ChatMessage.feedback.is_not(None)).order_by(ChatMessage.id.desc()).limit(limit)
    if rating != "all":
        q = q.where(ChatMessage.feedback == (-1 if rating == "down" else 1))
    emails = {u.id: u.email for u in db.scalars(select(User))}
    rows = [
        {
            "message_id": m.id, "rating": "up" if m.feedback == 1 else "down", "reason": m.feedback_reason,
            "question": question_for(m), "answer": m.content, "outcome": (m.meta or {}).get("outcome"),
            "sources": [s.get("document") for s in (m.meta or {}).get("sources", [])],
            "flags": (m.meta or {}).get("flags", []), "created_at": m.created_at.isoformat(),
            "user": emails.get(m.session.user_id),
        }
        for m in db.scalars(q)
    ]
    return {"up": counts.get(1, 0), "down": counts.get(-1, 0), "rows": rows}


@router.get("/escalations")
def staff_inbox(status: str = Query("open", pattern="^(open|resolved|all)$"), db: Session = Depends(get_db)):
    q = select(Escalation).order_by(Escalation.id.desc()).limit(200)
    if status != "all":
        q = q.where(Escalation.status == status)
    emails = {u.id: u.email for u in db.scalars(select(User))}
    return [{**escalation_out(e), "user": emails.get(e.user_id)} for e in db.scalars(q)]


class Resolution(BaseModel):
    reply: str = Field(min_length=1, max_length=2000)


@router.post("/escalations/{escalation_id}/resolve")
def resolve(escalation_id: int, body: Resolution, background: BackgroundTasks, db: Session = Depends(get_db)):
    e = db.get(Escalation, escalation_id)
    if e is None:
        raise HTTPException(404, "Not found")
    if e.status == "resolved":
        raise HTTPException(409, "Already answered")
    e.status, e.reply, e.resolved_at = "resolved", body.reply.strip(), datetime.now(timezone.utc)
    db.commit()
    user = db.get(User, e.user_id) if e.user_id else None
    if user:
        background.add_task(
            send_mail, user.email, "Your question to the Citewise team was answered",
            f"You asked:\n{e.question}\n\nThe team replied:\n{e.reply}\n",
        )
    return escalation_out(e)


def _safe_cell(value: object) -> str:
    """Stop spreadsheet formula injection: a cell that starts with = + - @ would be run by Excel as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/logs/export")
def export_audit_csv(
    days: int = Query(0, ge=0, le=3650), outcome: str | None = Query(None, pattern="^(answered|refused|blocked)$"),
    db: Session = Depends(get_db),
):
    """The audit ledger as CSV (questions are stored with personal data already redacted). `days=0` means all."""
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(50_000)
    if days:
        q = q.where(AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    header = ["id", "created_at", "request_id", "outcome", "question", "answer", "flags", "sources", "latency_ms", "model",
              "prompt_tokens", "completion_tokens", "user_id"]

    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        yield buf.getvalue()
        for r in db.scalars(q):
            buf.seek(0), buf.truncate()
            writer.writerow([
                r.id, r.created_at.isoformat(), r.request_id, r.outcome, _safe_cell(r.question), _safe_cell(r.answer),
                "|".join(r.flags or []), "|".join(r.sources or []), r.latency_ms, r.model, r.prompt_tokens,
                r.completion_tokens, r.user_id,
            ])
            yield buf.getvalue()

    name = f"citewise-audit-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(rows(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/retention/run")
def run_retention(db: Session = Depends(get_db)):
    """Apply RETENTION_DAYS and AUDIT_RETENTION_DAYS now instead of waiting for the next scheduled run."""
    return purge_expired(db)
