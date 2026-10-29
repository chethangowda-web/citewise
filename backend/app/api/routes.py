import logging
import secrets

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.llm.providers import LLMUnavailable
from app.models import Document
from app.observability import rate_limit
from app.rag.ingest import extract_text, ingest_document, title_from_filename
from app.rag.pipeline import answer_question

router = APIRouter()
log = logging.getLogger("rag.api")


def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = get_settings().api_key
    if expected and not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class ChatRequest(BaseModel):
    question: str = Field(max_length=5000)


class SourceOut(BaseModel):
    index: int
    document: str
    source: str
    snippet: str
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    flags: list[str]
    latency_ms: int
    outcome: str
    tokens: int = 0


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key), Depends(rate_limit)])
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    try:
        result = answer_question(db, req.question)
    except LLMUnavailable as e:
        log.warning("llm_unavailable", extra={"error": str(e)[:400]})  # root cause (quota, outage) for operators
        raise HTTPException(503, "The language model is temporarily unavailable. Please try again shortly.")
    return ChatResponse(
        answer=result.answer,
        sources=[SourceOut(**vars(s)) for s in result.sources],
        flags=result.flags,
        latency_ms=result.latency_ms,
        outcome=result.outcome,
        tokens=result.prompt_tokens + result.completion_tokens,
    )


@router.get("/documents", dependencies=[Depends(require_api_key)])
def list_documents(db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).order_by(Document.created_at.desc())).all()
    return [
        {"id": d.id, "title": d.title, "source": d.source, "chunks": len(d.chunks), "quarantined": sum(c.quarantined for c in d.chunks)}
        for d in docs
    ]


MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/documents", dependencies=[Depends(require_api_key), Depends(rate_limit)])
def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    name = file.filename or "upload.txt"
    if not name.lower().endswith((".pdf", ".md", ".txt")):
        raise HTTPException(400, "Only .pdf, .md and .txt files are supported")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 5 MB)")
    try:
        doc = ingest_document(db, name, title_from_filename(name), extract_text(name, data))
    except ValueError as e:
        raise HTTPException(422, str(e))
    held = sum(c.quarantined for c in doc.chunks)
    return {"id": doc.id, "title": doc.title, "chunks": len(doc.chunks), "quarantined": held}


@router.delete("/documents/{doc_id}", status_code=204, dependencies=[Depends(require_api_key), Depends(rate_limit)])
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
