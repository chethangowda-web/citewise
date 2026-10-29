import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AuditLog

router = APIRouter(prefix="/admin")


def require_admin(x_admin_key: str = Header(default="")) -> None:
    expected = get_settings().admin_api_key
    if not expected:  # no key configured: admin surface does not exist
        raise HTTPException(status_code=404, detail="Not found")
    if not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")


@router.get("/stats", dependencies=[Depends(require_admin)])
def stats(db: Session = Depends(get_db)):
    s = get_settings()
    row = db.execute(
        text(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE outcome = 'answered') AS answered,
                   count(*) FILTER (WHERE outcome = 'refused') AS refused,
                   count(*) FILTER (WHERE outcome = 'blocked') AS blocked,
                   count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS last_24h,
                   coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0) AS p50,
                   coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0) AS p95,
                   coalesce(sum(prompt_tokens), 0) AS tokens_in,
                   coalesce(sum(completion_tokens), 0) AS tokens_out
            FROM audit_logs
            """
        )
    ).one()
    flags = db.execute(
        text(
            "SELECT f AS flag, count(*) AS n FROM audit_logs, jsonb_array_elements_text(flags) AS f "
            "GROUP BY f ORDER BY n DESC"
        )
    ).all()
    cost = (row.tokens_in * s.price_in_per_m + row.tokens_out * s.price_out_per_m) / 1_000_000
    return {
        "total": row.total,
        "outcomes": {"answered": row.answered, "refused": row.refused, "blocked": row.blocked},
        "last_24h": row.last_24h,
        "latency_ms": {"p50": round(row.p50), "p95": round(row.p95)},
        "tokens": {"prompt": int(row.tokens_in), "completion": int(row.tokens_out)},
        "estimated_cost_usd": round(cost, 6),
        "guardrail_flags": {r.flag: r.n for r in flags},
        "guardrails_enabled": s.guardrails_enabled,
    }


@router.get("/logs", dependencies=[Depends(require_admin)])
def logs(
    limit: int = Query(50, ge=1, le=200),
    outcome: str | None = Query(None, pattern="^(answered|refused|blocked)$"),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "request_id": r.request_id,
            "outcome": r.outcome,
            "question": r.question,
            "answer": r.answer,
            "flags": r.flags,
            "sources": r.sources,
            "latency_ms": r.latency_ms,
            "model": r.model,
            "tokens": r.prompt_tokens + r.completion_tokens,
        }
        for r in db.scalars(q)
    ]
