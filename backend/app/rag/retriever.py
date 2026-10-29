import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chunk, Document

RRF_K = 60


@dataclass
class Retrieved:
    chunk_id: int
    document_title: str
    source: str
    content: str
    similarity: float  # cosine similarity to the query (0..1), used for the refusal threshold
    score: float  # ordering score: RRF value, or the reranker's score when reranking is on


def _row(chunk: Chunk, doc: Document, distance: float, score: float) -> Retrieved:
    return Retrieved(chunk.id, doc.title, doc.source, chunk.content, similarity=1 - float(distance), score=score)


def vector_search(db: Session, query_vec: list[float], k: int) -> list[Retrieved]:
    distance = Chunk.embedding.cosine_distance(query_vec)
    rows = db.execute(
        select(Chunk, Document, distance).join(Document).where(Chunk.quarantined.is_(False)).order_by(distance).limit(k)
    ).all()
    return [_row(c, d, dist, 0.0) for c, d, dist in rows]


def _or_query(text: str) -> str:
    words = sorted({w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1})
    return " | ".join(words)


def keyword_search(db: Session, query: str, query_vec: list[float], k: int, mode: str = "or") -> list[Retrieved]:
    """Postgres full-text search.

    mode="and": every query word must appear in the chunk (websearch semantics): precise but, for a
                natural-language question, it almost never matches.
    mode="or":  any query word may match, ranked by ts_rank_cd, so partial overlap still ranks.
    Each hit also carries its cosine similarity so the refusal floor can be applied uniformly.
    """
    ts_vec = func.to_tsvector("english", Chunk.content)
    if mode == "or":
        or_text = _or_query(query)
        if not or_text:
            return []
        ts_query = func.to_tsquery("english", or_text)
    else:
        ts_query = func.websearch_to_tsquery("english", query)
    distance = Chunk.embedding.cosine_distance(query_vec)
    rows = db.execute(
        select(Chunk, Document, distance)
        .join(Document)
        .where(ts_vec.op("@@")(ts_query), Chunk.quarantined.is_(False))
        .order_by(func.ts_rank_cd(ts_vec, ts_query).desc())
        .limit(k)
    ).all()
    return [_row(c, d, dist, 0.0) for c, d, dist in rows]


def fuse(*ranked_lists: list[Retrieved]) -> list[Retrieved]:
    """Reciprocal Rank Fusion over any number of ranked lists."""
    fused: dict[int, Retrieved] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked):
            if hit.chunk_id in fused:
                fused[hit.chunk_id].score += 1 / (RRF_K + rank)
            else:
                fused[hit.chunk_id] = Retrieved(**{**hit.__dict__, "score": 1 / (RRF_K + rank)})
    return sorted(fused.values(), key=lambda r: r.score, reverse=True)


def rerank_hits(query: str, hits: list[Retrieved], reranker) -> list[Retrieved]:
    scores = reranker.score(query, [h.content for h in hits])
    ranked = [Retrieved(**{**h.__dict__, "score": float(s)}) for h, s in zip(hits, scores)]
    return sorted(ranked, key=lambda r: r.score, reverse=True)


def hybrid_search(
    db: Session,
    query: str,
    query_vec: list[float],
    top_k: int,
    candidate_k: int,
    keyword_mode: str | None = None,
    rerank: bool | None = None,
    reranker=None,
) -> list[Retrieved]:
    """Vector + full-text search fused with RRF, optionally reranked by a cross-encoder."""
    s = get_settings()
    mode = keyword_mode or s.keyword_mode
    use_rerank = s.rerank_enabled if rerank is None else rerank

    fused = fuse(vector_search(db, query_vec, candidate_k), keyword_search(db, query, query_vec, candidate_k, mode))
    if use_rerank and fused:
        if reranker is None:
            from app.rag.rerank import get_reranker

            reranker = get_reranker()
        fused = rerank_hits(query, fused[: s.rerank_candidates], reranker)
    return fused[:top_k]
