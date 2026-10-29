from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Chunk, Document

RRF_K = 60


@dataclass
class Retrieved:
    chunk_id: int
    document_title: str
    source: str
    content: str
    similarity: float  # cosine similarity to the query (0..1), used for the refusal threshold
    score: float  # fused RRF score, used for ordering


def hybrid_search(db: Session, query: str, query_vec: list[float], top_k: int, candidate_k: int) -> list[Retrieved]:
    """Vector search + Postgres full-text search, merged with Reciprocal Rank Fusion."""
    distance = Chunk.embedding.cosine_distance(query_vec)
    vec_rows = db.execute(
        select(Chunk, Document, distance.label("d"))
        .join(Document)
        .order_by(distance)
        .limit(candidate_k)
    ).all()

    ts_query = func.websearch_to_tsquery("english", query)
    ts_vec = func.to_tsvector("english", Chunk.content)
    kw_rows = db.execute(
        select(Chunk, Document)
        .join(Document)
        .where(ts_vec.op("@@")(ts_query))
        .order_by(func.ts_rank(ts_vec, ts_query).desc())
        .limit(candidate_k)
    ).all()

    fused: dict[int, Retrieved] = {}
    for rank, (chunk, doc, d) in enumerate(vec_rows):
        fused[chunk.id] = Retrieved(
            chunk.id, doc.title, doc.source, chunk.content, similarity=1 - float(d), score=1 / (RRF_K + rank)
        )
    for rank, (chunk, doc) in enumerate(kw_rows):
        if chunk.id in fused:
            fused[chunk.id].score += 1 / (RRF_K + rank)
        # keyword-only hits have no similarity measurement; they are skipped so the
        # refusal threshold stays meaningful.

    return sorted(fused.values(), key=lambda r: r.score, reverse=True)[:top_k]
