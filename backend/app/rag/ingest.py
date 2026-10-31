"""Ingest documents: python -m app.rag.ingest ../data/policies"""
import hashlib
import io
import sys
from datetime import date
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import logging

from app.config import get_settings
from app.guardrails.document_scan import scan_chunk
from app.db import SessionLocal, init_db
from app.llm.providers import get_embedder
from app.models import Chunk, Document
from app.rag.chunking import chunk_text


def title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").title()


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="replace")


def ingest_document(
    db: Session,
    source: str,
    title: str,
    text: str,
    owner_id: int | None = None,
    team_id: int | None = None,
    effective_date: date | None = None,
) -> Document:
    """File a document. Re-ingesting a source (in the same scope) with identical text changes nothing;
    with changed text the old one becomes a superseded version and the new one is current.

    The returned document has `unchanged` set to True when nothing had to be done.
    """
    s = get_settings()
    pieces = chunk_text(text, s.chunk_size, s.chunk_overlap)
    if not pieces:
        raise ValueError("Document contains no extractable text")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    owner_match = Document.owner_id.is_(None) if owner_id is None else Document.owner_id == owner_id
    team_match = Document.team_id.is_(None) if team_id is None else Document.team_id == team_id
    existing = db.scalar(select(Document).where(Document.source == source, owner_match, team_match, Document.status == "current"))
    if existing is not None and existing.content_hash == digest:
        if effective_date is not None and existing.effective_date != effective_date:
            existing.effective_date = effective_date  # same text, newly confirmed date: no new version needed
            db.commit()
        existing.unchanged = True  # type: ignore[attr-defined]
        return existing
    vectors = get_embedder().embed(pieces)
    version = 1
    if existing is not None:
        latest = db.scalar(select(func.max(Document.version)).where(Document.source == source, owner_match, team_match)) or 1
        version = latest + 1
        existing.status = "superseded"
        db.flush()  # free the unique slot before the new current version is inserted
    doc = Document(
        title=title, source=source, owner_id=owner_id, team_id=team_id,
        version=version, effective_date=effective_date, content_hash=digest,
    )
    doc.unchanged = False  # type: ignore[attr-defined]
    scans = [scan_chunk(p) for p in pieces]
    doc.chunks = [
        Chunk(position=i, content=p, embedding=v, quarantined=bool(reasons))
        for i, (p, v, reasons) in enumerate(zip(pieces, vectors, scans))
    ]
    flagged = [r for r in scans if r]
    if flagged:
        logging.getLogger("rag.ingest").warning(
            "chunks_quarantined", extra={"source": source, "quarantined": len(flagged), "reasons": sorted({x for r in flagged for x in r})}
        )
    db.add(doc)
    db.commit()
    return doc


def main(folder: str) -> None:
    init_db()
    with SessionLocal() as db:
        for path in sorted(Path(folder).glob("*")):
            if path.suffix.lower() not in {".md", ".txt", ".pdf"}:
                continue
            doc = ingest_document(db, path.name, title_from_filename(path.name), extract_text(path.name, path.read_bytes()))
            held = sum(c.quarantined for c in doc.chunks)
            print(f"ingested {path.name}: {len(doc.chunks)} chunks" + (f"  ({held} QUARANTINED: possible prompt injection)" if held else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../data/policies")
