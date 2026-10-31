"""Daily token allowances. Every answered question spends model tokens, and free-tier quotas (and real bills)
are finite, so one person or one runaway script must not be able to use up everything.

Usage is read from the audit ledger over a sliding 24 hours, so there is no separate counter to drift or reset.
Concurrent requests can overshoot a limit slightly; this is a budget guard, not an exact meter.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, User

WINDOW = timedelta(hours=24)


def tokens_used(db: Session, user_id: int | None = None, since: datetime | None = None) -> int:
    since = since or datetime.now(timezone.utc) - WINDOW
    q = select(func.coalesce(func.sum(AuditLog.prompt_tokens + AuditLog.completion_tokens), 0)).where(AuditLog.created_at >= since)
    if user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    return int(db.scalar(q) or 0)


def _retry_after(db: Session, user_id: int | None) -> int:
    """Seconds until the oldest spend in the window ages out: the earliest moment any room can open up."""
    since = datetime.now(timezone.utc) - WINDOW
    q = select(func.min(AuditLog.created_at)).where(AuditLog.created_at >= since, AuditLog.prompt_tokens + AuditLog.completion_tokens > 0)
    if user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    oldest = db.scalar(q)
    return max(60, int((oldest + WINDOW - datetime.now(timezone.utc)).total_seconds()) + 1) if oldest else 3600


def enforce_budget(db: Session, user: User | None) -> None:
    """Refuse (429) before any model call is made when a limit is already spent."""
    s = get_settings()
    if s.global_daily_token_limit > 0 and tokens_used(db) >= s.global_daily_token_limit:
        raise HTTPException(
            429, "The assistant has reached its capacity for today. Please try again later.",
            headers={"Retry-After": str(_retry_after(db, None))},
        )
    if user is not None and user.role != "admin" and s.user_daily_token_limit > 0:  # admins keep working
        if tokens_used(db, user.id) >= s.user_daily_token_limit:
            raise HTTPException(
                429, "You have used your daily allowance of questions. It frees up gradually over the next 24 hours.",
                headers={"Retry-After": str(_retry_after(db, user.id))},
            )


def my_usage(db: Session, user: User | None) -> dict:
    s = get_settings()
    used = tokens_used(db, user.id) if user else tokens_used(db)
    limit = s.user_daily_token_limit if user else s.global_daily_token_limit
    exempt = bool(user and user.role == "admin")
    return {"used": used, "limit": 0 if exempt else limit, "unlimited": exempt or limit <= 0}
