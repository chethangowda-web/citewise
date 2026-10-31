import json
import logging
import secrets
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.auth import get_current_user, optional_user, require_role
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.llm.providers import LLMUnavailable
from app.models import ChatMessage, ChatSession, Document, Team, TeamMember, User
from app.observability import rate_limit
from app.rag.extract import SUPPORTED_EXTENSIONS
from app.rag.ingest import extract_text, ingest_document, title_from_filename
from app.rag.pipeline import ChatResult, answer_question, run_pipeline
from app.rag.retriever import document_updated, visible_to
from app.usage import enforce_budget, my_usage

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
    updated_at: str = ""
    version: int = 1
    stale: bool = False


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    flags: list[str]
    latency_ms: int
    outcome: str
    tokens: int = 0
    session_id: str = ""
    interpreted_as: str = ""
    message_id: int = 0  # the answer, for feedback and escalation


def _uid(user: User | None) -> int | None:
    return user.id if user else None


def _own_session(db: Session, session_id: str, user: User | None) -> ChatSession:
    sess = db.get(ChatSession, session_id)
    if sess is None or sess.user_id != _uid(user):  # someone else's session looks the same as a missing one
        raise HTTPException(404, "Conversation not found")
    return sess


def session_history(sess: ChatSession | None) -> list[tuple[str, str]]:
    """Earlier (question, answer) pairs of a conversation, oldest first, for understanding follow-ups.

    Blocked turns are left out: a question the guardrails rejected must not steer the next one.
    """
    if sess is None:
        return []
    msgs = sess.messages
    pairs = []
    for q, a in zip(msgs[0::2], msgs[1::2]):
        if (a.meta or {}).get("outcome") != "blocked":
            pairs.append((q.content, a.content))
    return pairs[-max(get_settings().memory_turns, 0) :] if get_settings().memory_turns > 0 else []


def save_turn(db: Session, user: User | None, session_id: str | None, result: ChatResult) -> tuple[str, int]:
    """Append this exchange to a conversation (creating it if needed); returns (conversation id, answer message id)."""
    sess = _own_session(db, session_id, user) if session_id else None
    if sess is None:
        sess = ChatSession(id=uuid.uuid4().hex, user_id=_uid(user), title=(result.question or "New conversation")[:80])
        db.add(sess)
    sess.updated_at = datetime.now(timezone.utc)
    meta = {
        "sources": [vars(x) for x in result.sources], "flags": result.flags, "outcome": result.outcome,
        "latency_ms": result.latency_ms, "tokens": result.prompt_tokens + result.completion_tokens,
        "interpreted_as": result.interpreted_as,
    }
    sess.messages.append(ChatMessage(role="user", content=result.question))
    answer = ChatMessage(role="assistant", content=result.answer, meta=meta)
    sess.messages.append(answer)
    db.commit()
    return sess.id, answer.id


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key), Depends(rate_limit)])
def chat(req: ChatRequest, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    sess = _own_session(db, req.session_id, user) if req.session_id else None  # fail before spending an LLM call
    enforce_budget(db, user)
    try:
        result = answer_question(db, req.question, _uid(user), history=session_history(sess))
    except LLMUnavailable as e:
        log.warning("llm_unavailable", extra={"error": str(e)[:400]})  # root cause (quota, outage) for operators
        raise HTTPException(503, "The language model is temporarily unavailable. Please try again shortly.")
    return _response(result, *save_turn(db, user, req.session_id, result))


def _response(result: ChatResult, session_id: str, message_id: int = 0) -> ChatResponse:
    return ChatResponse(
        answer=result.answer,
        sources=[SourceOut(**vars(s)) for s in result.sources],
        flags=result.flags,
        latency_ms=result.latency_ms,
        outcome=result.outcome,
        tokens=result.prompt_tokens + result.completion_tokens,
        session_id=session_id,
        interpreted_as=result.interpreted_as,
        message_id=message_id,
    )


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream", dependencies=[Depends(require_api_key), Depends(rate_limit)])
def chat_stream(req: ChatRequest, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    """Server-sent events: `stage` as the pipeline advances, `draft` (full text so far, PII-redacted) while the
    model writes, then `done` with the verified answer, which replaces the draft. `error` if the model is down."""
    sess = _own_session(db, req.session_id, user) if req.session_id else None  # a plain 404 before the stream opens
    history = session_history(sess)  # read now: the request session may be closed before the stream runs
    enforce_budget(db, user)  # a plain 429 before the stream opens

    def events():
        with SessionLocal() as stream_db:  # the request-scoped session may be closed before the stream ends
            try:
                for kind, payload in run_pipeline(stream_db, req.question, _uid(user), stream=True, history=history):
                    if kind == "result":
                        sid, mid = save_turn(stream_db, user, req.session_id, payload)  # type: ignore[arg-type]
                        yield _sse("done", _response(payload, sid, mid).model_dump())  # type: ignore[arg-type]
                    else:
                        yield _sse(kind, {"stage" if kind == "stage" else "text": payload})
            except LLMUnavailable as e:
                log.warning("llm_unavailable", extra={"error": str(e)[:400]})
                yield _sse("error", {"detail": "The language model is temporarily unavailable. Please try again shortly."})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/usage/me", dependencies=[Depends(require_api_key)])
def usage_me(db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    """Tokens spent in the last 24 hours against your allowance."""
    return my_usage(db, user)


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
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "meta": m.meta, "feedback": m.feedback}
            for m in sess.messages
        ],
    }


@router.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_session(session_id: str, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    db.delete(_own_session(db, session_id, user))
    db.commit()


def doc_scope(d: Document) -> str:
    if d.team_id is not None:
        return "team"
    return "private" if d.owner_id is not None else "shared"


def _lineage(d: Document):
    """SQL condition matching every version of the same file in the same scope."""
    owner = Document.owner_id.is_(None) if d.owner_id is None else Document.owner_id == d.owner_id
    team = Document.team_id.is_(None) if d.team_id is None else Document.team_id == d.team_id
    return (Document.source == d.source) & owner & (team)


def _is_admin(user: User | None, admin_key: str) -> bool:
    expected = get_settings().admin_api_key
    if expected and admin_key and secrets.compare_digest(admin_key, expected):
        return True
    return user is not None and user.role == "admin"


@router.get("/documents", dependencies=[Depends(require_api_key)])
def list_documents(db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    docs = db.scalars(select(Document).where(visible_to(_uid(user))).order_by(Document.created_at.desc())).all()
    team_names = {t.id: t.name for t in db.scalars(select(Team))}
    counts = {
        (o, t, src): n
        for o, t, src, n in db.execute(
            select(Document.owner_id, Document.team_id, Document.source, func.count()).group_by(
                Document.owner_id, Document.team_id, Document.source
            )
        )
    }
    days = get_settings().stale_after_days
    now = datetime.now(timezone.utc)
    out = []
    for d in docs:
        updated = document_updated(d)
        out.append({
            "id": d.id, "title": d.title, "source": d.source, "chunks": len(d.chunks),
            "quarantined": sum(c.quarantined for c in d.chunks), "private": d.owner_id is not None or d.team_id is not None,
            "scope": doc_scope(d), "team": team_names.get(d.team_id),
            "version": d.version, "versions": counts.get((d.owner_id, d.team_id, d.source), 1),
            "updated_at": updated.date().isoformat(), "stale": bool(days > 0 and (now - updated).days > days),
        })
    return out


MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/documents", dependencies=[Depends(require_api_key), Depends(rate_limit)])
def upload_document(
    file: UploadFile = File(...),
    scope: str = Form("private"),
    team_id: int | None = Form(None),
    effective_date: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_role("editor")),
):
    """Who may see the upload: `private` (only the uploader), `team` (members of team_id) or `shared`
    (everyone; admins only). With auth disabled everything is shared, as before accounts existed."""
    owner_id: int | None = _uid(user)
    target_team: int | None = None
    if user is not None:
        if scope == "shared":
            if user.role != "admin":
                raise HTTPException(403, "Only admins can share a document with everyone")
            owner_id = None
        elif scope == "team":
            member = db.scalar(select(TeamMember).where(TeamMember.user_id == user.id, TeamMember.team_id == team_id))
            if team_id is None or db.get(Team, team_id) is None or (member is None and user.role != "admin"):
                raise HTTPException(403, "You can only share with a team you belong to")
            target_team = team_id
        elif scope != "private":
            raise HTTPException(422, "scope must be private, team or shared")
    effective: date | None = None
    if effective_date:
        try:
            effective = date.fromisoformat(effective_date)
        except ValueError:
            raise HTTPException(422, "effective_date must look like 2026-03-01")
    name = file.filename or "upload.txt"
    if not name.lower().endswith(SUPPORTED_EXTENSIONS):
        raise HTTPException(400, "Only .pdf, .docx, .html, .md and .txt files are supported")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 5 MB)")
    try:
        doc = ingest_document(db, name, title_from_filename(name), extract_text(name, data), owner_id, target_team, effective)
    except ValueError as e:
        raise HTTPException(422, str(e))
    held = sum(c.quarantined for c in doc.chunks)
    return {
        "id": doc.id, "title": doc.title, "chunks": len(doc.chunks), "quarantined": held,
        "private": doc.owner_id is not None or doc.team_id is not None, "scope": doc_scope(doc),
        "version": doc.version, "unchanged": bool(getattr(doc, "unchanged", False)),
    }


@router.delete("/documents/{doc_id}", status_code=204, dependencies=[Depends(require_api_key), Depends(rate_limit)])
def delete_document(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
    x_admin_key: str = Header(default=""),
):
    admin = _is_admin(user, x_admin_key)
    if user is None and not admin and get_settings().auth_enabled:  # the admin key alone is enough for admin work
        raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
    doc = db.get(Document, doc_id)
    if doc is not None and user is not None and not admin:
        in_team = doc.team_id is not None and db.scalar(
            select(TeamMember).where(TeamMember.user_id == user.id, TeamMember.team_id == doc.team_id)
        )
        if doc.owner_id not in (None, user.id) and not in_team:
            doc = None  # a document the user cannot see looks missing
    if doc is None:
        raise HTTPException(404, "Document not found")
    if user is not None and not admin and doc.owner_id != user.id:
        raise HTTPException(403, "Only the uploader or an admin can remove this document")
    # Removing a document removes its whole history: leaving archived versions behind would keep text the
    # owner meant to delete.
    db.execute(delete(Document).where(_lineage(doc)))  # chunks go with them (ON DELETE CASCADE)
    db.commit()
