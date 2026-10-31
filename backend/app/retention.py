"""Deleting old data on a schedule, so personal data is not kept longer than the operator decided."""
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, ChatSession

log = logging.getLogger("rag.retention")


def purge_expired(db: Session) -> dict:
    """Delete conversations and audit rows older than the configured limits. Returns how many of each went."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    removed = {"sessions": 0, "audit": 0}
    if s.retention_days > 0:  # messages go with their conversation (ON DELETE CASCADE)
        removed["sessions"] = db.execute(
            delete(ChatSession).where(ChatSession.updated_at < now - timedelta(days=s.retention_days))
        ).rowcount
    if s.audit_retention_days > 0:
        removed["audit"] = db.execute(
            delete(AuditLog).where(AuditLog.created_at < now - timedelta(days=s.audit_retention_days))
        ).rowcount
    db.commit()
    if any(removed.values()):
        log.info("retention_purged", extra=removed)
    return removed


def start_retention_thread() -> threading.Event | None:
    """Run the purge now and then every RETENTION_INTERVAL_HOURS. Returns the event that stops it, or None
    when no retention limit is configured."""
    s = get_settings()
    if s.retention_days <= 0 and s.audit_retention_days <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                with SessionLocal() as db:
                    purge_expired(db)
            except Exception:  # a failed purge must never take the API down; it tries again next round
                log.exception("retention_failed")
            stop.wait(max(s.retention_interval_hours, 1) * 3600)

    threading.Thread(target=loop, name="retention", daemon=True).start()
    return stop
