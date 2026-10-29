"""Retrieval ablation: which retrieval strategy actually finds the right passage?

Runs inside the backend container against a scratch database (never your real one):

    docker compose run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://rag:rag@db:5432/rag_lab \
        backend python -m eval.retrieval_lab

It ingests data/eval_corpus (24 documents) through the real ingestion code, embeds the 74 gold queries,
and scores every method on Hit@k and MRR. A retrieved chunk is relevant when it comes from the gold
document AND contains the gold answer snippet. Only embeddings are spent; no LLM tokens.
"""
import hashlib
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.llm.providers import get_embedder
from app.rag.ingest import extract_text, ingest_document, title_from_filename
from app.rag.rerank import get_reranker
from app.rag.retriever import RRF_K, Retrieved, fuse, keyword_search, rerank_hits, vector_search

CORPUS = Path("/data/eval_corpus")
QUERIES = Path(__file__).parent / "retrieval_queries.json"
OUT_MD = Path(__file__).parent / "RETRIEVAL.md"
OUT_JSON = Path(__file__).parent / "retrieval_report.json"
CACHE = Path(__file__).parent / ".cache" / "query_embeddings.json"
CAND = 20  # candidates fetched per branch
DEPTH = 10  # ranks scored
METHODS = ["vector", "keyword_and", "keyword_or", "legacy_hybrid", "hybrid_and", "hybrid_or", "vector+rerank", "hybrid_or+rerank"]
LABEL = {
    "vector": "Vector only",
    "keyword_and": "Keyword only (AND)",
    "keyword_or": "Keyword only (OR)",
    "legacy_hybrid": "Hybrid, previous behaviour*",
    "hybrid_and": "Hybrid, AND keywords",
    "hybrid_or": "Hybrid, OR keywords",
    "vector+rerank": "Vector + reranker",
    "hybrid_or+rerank": "Hybrid OR + reranker",
}


def embed_queries(questions: list[str]) -> list[list[float]]:
    """Embed the gold queries in small paced batches (the free tier limits requests per minute) and
    cache them on disk, so re-running the lab costs no embedding quota."""
    key = lambda q: hashlib.sha256(f"{get_settings().embedding_model}|{q}".encode()).hexdigest()  # noqa: E731
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    missing = [q for q in dict.fromkeys(questions) if key(q) not in cache]
    for i in range(0, len(missing), 25):
        batch = missing[i : i + 25]
        for attempt in range(6):
            try:
                vectors = get_embedder().embed(batch, is_query=True)
                break
            except Exception as e:  # noqa: BLE001 - quota errors surface as several SDK exception types
                if attempt == 5:
                    raise
                print(f"  embedding quota hit ({type(e).__name__}); waiting 30s", flush=True)
                time.sleep(30)
        cache.update({key(q): v for q, v in zip(batch, vectors)})
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache))
        print(f"  embedded {min(i + 25, len(missing))}/{len(missing)} new queries", flush=True)
        if i + 25 < len(missing):
            time.sleep(20)
    return [cache[key(q)] for q in questions]


def legacy_hybrid(db, query, qvec) -> list[Retrieved]:
    """The retriever as it shipped before this lab: keyword matches could only boost vector hits."""
    vec = vector_search(db, qvec, CAND)
    kw_rank = {h.chunk_id: r for r, h in enumerate(keyword_search(db, query, qvec, CAND, "and"))}
    scored = []
    for r, h in enumerate(vec):
        s = 1 / (RRF_K + r) + (1 / (RRF_K + kw_rank[h.chunk_id]) if h.chunk_id in kw_rank else 0)
        scored.append(Retrieved(**{**h.__dict__, "score": s}))
    return sorted(scored, key=lambda x: x.score, reverse=True)


def run_method(method, db, query, qvec, reranker) -> list[Retrieved]:
    if method == "vector":
        return vector_search(db, qvec, CAND)
    if method == "keyword_and":
        return keyword_search(db, query, qvec, CAND, "and")
    if method == "keyword_or":
        return keyword_search(db, query, qvec, CAND, "or")
    if method == "legacy_hybrid":
        return legacy_hybrid(db, query, qvec)
    if method == "hybrid_and":
        return fuse(vector_search(db, qvec, CAND), keyword_search(db, query, qvec, CAND, "and"))
    if method == "hybrid_or":
        return fuse(vector_search(db, qvec, CAND), keyword_search(db, query, qvec, CAND, "or"))
    if method == "vector+rerank":
        return rerank_hits(query, vector_search(db, qvec, CAND), reranker)
    if method == "hybrid_or+rerank":
        fused = fuse(vector_search(db, qvec, CAND), keyword_search(db, query, qvec, CAND, "or"))
        return rerank_hits(query, fused[:CAND], reranker)
    raise ValueError(method)


def first_relevant_rank(hits: list[Retrieved], gold: dict) -> int | None:
    for rank, h in enumerate(hits[:DEPTH], 1):
        if h.source == gold["source"] and gold["snippet"] in h.content:
            return rank
    return None


def metrics(ranks: list[int | None]) -> dict:
    n = len(ranks)
    hit = lambda k: sum(r is not None and r <= k for r in ranks) / n  # noqa: E731
    return {"n": n, "hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "mrr": sum(1 / r for r in ranks if r) / n}


def main() -> None:
    init_db()
    with SessionLocal() as db:
        files = sorted(CORPUS.glob("*.md"))
        have = db.scalar(text("SELECT count(*) FROM documents"))
        if have == len(files) and "--reingest" not in sys.argv:
            print(f"reusing the {have} documents already ingested (pass --reingest to rebuild)")
        else:
            db.execute(text("TRUNCATE chunks, documents RESTART IDENTITY CASCADE"))
            db.commit()
            for f in files:
                ingest_document(db, f.name, title_from_filename(f.name), extract_text(f.name, f.read_bytes()))
        n_chunks = db.scalar(text("SELECT count(*) FROM chunks"))
        print(f"{len(files)} documents, {n_chunks} chunks")

        queries = json.loads(QUERIES.read_text(encoding="utf-8"))
        qvecs = embed_queries([q["question"] for q in queries])
        reranker = get_reranker()
        reranker.score("warm up", ["warm up the model before timing"])

        ranks: dict[str, list[int | None]] = {m: [] for m in METHODS}
        latency: dict[str, list[float]] = {m: [] for m in METHODS}
        for q, qvec in zip(queries, qvecs):
            for m in METHODS:
                t = time.perf_counter()
                hits = run_method(m, db, q["question"], qvec, reranker)
                latency[m].append((time.perf_counter() - t) * 1000)
                ranks[m].append(first_relevant_rank(hits, q))

    overall = {m: metrics(ranks[m]) for m in METHODS}
    types = sorted({q["type"] for q in queries})
    by_type = {m: {t: metrics([r for r, q in zip(ranks[m], queries) if q["type"] == t]) for t in types} for m in METHODS}
    base = ranks["legacy_hybrid"]
    wins = {
        m: (
            sum((r or 99) < (b or 99) for r, b in zip(ranks[m], base)),
            sum((r or 99) > (b or 99) for r, b in zip(ranks[m], base)),
        )
        for m in METHODS
    }
    lat = {m: statistics.mean(latency[m]) for m in METHODS}

    OUT_JSON.write_text(
        json.dumps({"documents": len(files), "chunks": n_chunks, "queries": len(queries), "overall": overall,
                    "by_type": by_type, "latency_ms": lat, "vs_legacy": wins,
                    "misses": {m: [q["id"] for r, q in zip(ranks[m], queries) if r is None] for m in METHODS}}, indent=1),
        encoding="utf-8",
    )

    pct = lambda x: f"{x:.0%}"  # noqa: E731
    counts = {t: sum(q["type"] == t for q in queries) for t in types}
    lines = [
        f"Corpus: {len(files)} documents, {n_chunks} chunks. Queries: {len(queries)} ("
        + ", ".join(f"{counts[t]} {t}" for t in types) + ").",
        "",
        "| Method | Hit@1 | Hit@3 | Hit@5 | MRR | Beats / loses to previous* | Retrieval latency |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in METHODS:
        o, (w, l) = overall[m], wins[m]
        versus = "-" if m == "legacy_hybrid" else f"{w} / {l}"
        lines.append(f"| {LABEL[m]} | {pct(o['hit@1'])} | {pct(o['hit@3'])} | {pct(o['hit@5'])} | {o['mrr']:.3f} | {versus} | {lat[m]:.0f} ms |")
    lines += ["", "Hit@1 by query type:", "", "| Method | " + " | ".join(f"{t} ({counts[t]})" for t in types) + " |", "|---|" + "---|" * len(types)]
    for m in METHODS:
        lines.append(f"| {LABEL[m]} | " + " | ".join(pct(by_type[m][t]["hit@1"]) for t in types) + " |")
    lines += ["", "\\* the retriever as it shipped before this lab: full-text matches (AND semantics) could only boost vector hits."]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwrote {OUT_MD.name} and {OUT_JSON.name}")


if __name__ == "__main__":
    main()
