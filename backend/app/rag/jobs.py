"""Filing big uploads in the background, so the request that carried the file can return at once.

Jobs run in the API process (FastAPI background tasks), which is right for one server: a restart loses the
queue, so startup marks anything unfinished as failed instead of leaving it "working" forever.
"""
import logging
import threading
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.llm.providers import LLMUnavailable
from app.models import Document, IngestJob
from app.rag.extract import extract_text
from app.rag.ingest import ingest_document, title_from_filename

log = logging.getLogger("rag.jobs")

_semaphores: dict[int, threading.BoundedSemaphore] = {}
_lock = threading.Lock()


def _slots() -> threading.BoundedSemaphore:
    """At most INGEST_CONCURRENCY documents are embedded at once: the embedding API has rate limits, and a
    handful of big uploads must not starve answering questions of them."""
    limit = max(get_settings().ingest_concurrency, 1)
    with _lock:
        return _semaphores.setdefault(limit, threading.BoundedSemaphore(limit))


def doc_summary(doc: Document) -> dict:
    """What an upload reports, whether it ran straight away or as a job."""
    scope = "team" if doc.team_id is not None else ("private" if doc.owner_id is not None else "shared")
    return {
        "id": doc.id, "title": doc.title, "chunks": len(doc.chunks), "quarantined": sum(c.quarantined for c in doc.chunks),
        "private": scope != "shared", "scope": scope, "version": doc.version,
        "unchanged": bool(getattr(doc, "unchanged", False)),
    }


def run_ingest_job(
    job_id: str, name: str, data: bytes, owner_id: int | None, team_id: int | None, effective_date: date | None
) -> None:
    with _slots():  # waiting for a slot is the "queued" state
        with SessionLocal() as db:
            job = db.get(IngestJob, job_id)
            job.status = "running"
            db.commit()
            status, error, result = "done", "", None
            try:
                doc = ingest_document(db, name, title_from_filename(name), extract_text(name, data), owner_id, team_id, effective_date)
                result = doc_summary(doc)
            except ValueError as e:  # an unreadable or empty file: say so, the way the direct upload would
                db.rollback()
                status, error = "failed", str(e)
            except LLMUnavailable as e:  # the embedding service stayed rate-limited or down
                db.rollback()
                status, error = "failed", str(e)
            except Exception:  # never leave the job hanging, and never leak internals to the page
                db.rollback()
                log.exception("ingest_job_failed", extra={"job_id": job_id, "upload_name": name})
                status, error = "failed", "Something went wrong while filing this document. Please try again."
            job = db.get(IngestJob, job_id)
            job.status, job.error, job.result, job.finished_at = status, error, result, datetime.now(timezone.utc)
            db.commit()


def recover_interrupted_jobs() -> int:
    """Called at startup: jobs that were queued or running when the process stopped will never finish."""
    with SessionLocal() as db:
        n = db.execute(
            update(IngestJob).where(IngestJob.status.in_(["queued", "running"])).values(
                status="failed", error="The server restarted while this was being filed. Please upload it again.",
                finished_at=datetime.now(timezone.utc),
            )
        ).rowcount
        db.commit()
    if n:
        log.warning("ingest_jobs_interrupted", extra={"count": n})
    return n


def prune_jobs(db: Session, keep_days: int = 7) -> None:
    db.execute(delete(IngestJob).where(IngestJob.finished_at < datetime.now(timezone.utc) - timedelta(days=keep_days)))
    db.commit()
