import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.llm.providers import LLMUnavailable
from app.models import ChatMessage, ChatSession, Document, User
from app.observability import rate_limit
from app.rag.ingest import extract_text, ingest_document, title_from_filename
from app.rag.pipeline import ChatResult, answer_question
from app.rag.retriever import visible_to

router = APIRouter()
log = logging.getLogger("rag.api")


def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = get_settings().api_key
    if expected and not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class ChatRequest(BaseModel):
    question: str = Field(max_length=5000)
    session_id: str | None = Field(default=None, max_length=32)  # omit to start a new conversation


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
    session_id: str = ""


def _uid(user: User | None) -> int | None:
    return user.id if user else None


def _own_session(db: Session, session_id: str, user: User | None) -> ChatSession:
    sess = db.get(ChatSession, session_id)
    if sess is None or sess.user_id != _uid(user):  # someone else's session looks the same as a missing one
        raise HTTPException(404, "Conversation not found")
    return sess


def save_turn(db: Session, user: User | None, session_id: str | None, result: ChatResult) -> str:
    """Append this exchange to a conversation (creating it if needed) and return its id."""
    sess = _own_session(db, session_id, user) if session_id else None
    if sess is None:
        sess = ChatSession(id=uuid.uuid4().hex, user_id=_uid(user), title=(result.question or "New conversation")[:80])
        db.add(sess)
    sess.updated_at = datetime.now(timezone.utc)
    meta = {
        "sources": [vars(x) for x in result.sources], "flags": result.flags, "outcome": result.outcome,
        "latency_ms": result.latency_ms, "tokens": result.prompt_tokens + result.completion_tokens,
    }
    sess.messages.append(ChatMessage(role="user", content=result.question))
    sess.messages.append(ChatMessage(role="assistant", content=result.answer, meta=meta))
    db.commit()
    return sess.id


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key), Depends(rate_limit)])
def chat(req: ChatRequest, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    if req.session_id:
        _own_session(db, req.session_id, user)  # fail before spending an LLM call
    try:
        result = answer_question(db, req.question, _uid(user))
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
        session_id=save_turn(db, user, req.session_id, result),
    )


@router.get("/sessions", dependencies=[Depends(require_api_key)])
def list_sessions(db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    uid = _uid(user)
    owner = ChatSession.user_id.is_(None) if uid is None else ChatSession.user_id == uid
    rows = db.scalars(select(ChatSession).where(owner).order_by(ChatSession.updated_at.desc()).limit(100)).all()
    return [{"id": r.id, "title": r.title, "updated_at": r.updated_at.isoformat()} for r in rows]


@router.get("/sessions/{session_id}", dependencies=[Depends(require_api_key)])
def get_session(session_id: str, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    sess = _own_session(db, session_id, user)
    return {
        "id": sess.id,
        "title": sess.title,
        "messages": [{"id": m.id, "role": m.role, "content": m.content, "meta": m.meta} for m in sess.messages],
    }


@router.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_session(session_id: str, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    db.delete(_own_session(db, session_id, user))
    db.commit()


@router.get("/documents", dependencies=[Depends(require_api_key)])
def list_documents(db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    docs = db.scalars(select(Document).where(visible_to(_uid(user))).order_by(Document.created_at.desc())).all()
    return [
        {
            "id": d.id, "title": d.title, "source": d.source, "chunks": len(d.chunks),
            "quarantined": sum(c.quarantined for c in d.chunks), "private": d.owner_id is not None,
        }
        for d in docs
    ]


MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/documents", dependencies=[Depends(require_api_key), Depends(rate_limit)])
def upload_document(
    file: UploadFile = File(...), db: Session = Depends(get_db), user: User | None = Depends(get_current_user)
):
    name = file.filename or "upload.txt"
    if not name.lower().endswith((".pdf", ".md", ".txt")):
        raise HTTPException(400, "Only .pdf, .md and .txt files are supported")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 5 MB)")
    try:
        doc = ingest_document(db, name, title_from_filename(name), extract_text(name, data), _uid(user))
    except ValueError as e:
        raise HTTPException(422, str(e))
    held = sum(c.quarantined for c in doc.chunks)
    return {"id": doc.id, "title": doc.title, "chunks": len(doc.chunks), "quarantined": held, "private": doc.owner_id is not None}


@router.delete("/documents/{doc_id}", status_code=204, dependencies=[Depends(require_api_key), Depends(rate_limit)])
def delete_document(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
    x_admin_key: str = Header(default=""),
):
    doc = db.get(Document, doc_id)
    if doc is None or doc.owner_id not in (None, _uid(user)):  # another user's private document looks missing
        raise HTTPException(404, "Document not found")
    if doc.owner_id is None and user is not None:  # shared documents are curated by an admin, not by users
        admin = get_settings().admin_api_key
        if not (admin and secrets.compare_digest(x_admin_key, admin)):
            raise HTTPException(403, "Shared documents can only be removed by an admin")
    db.delete(doc)
    db.commit()
