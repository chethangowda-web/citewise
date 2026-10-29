"""Choose the refusal similarity threshold from data.

Run inside the backend container (needs DB + embeddings):
    python -m eval.tune_threshold
Prints the top-1 similarity for every golden question and the threshold that best
separates answerable questions from ones that should be refused.
"""
import json
from pathlib import Path

from app.db import SessionLocal
from app.llm.providers import get_embedder
from app.rag.retriever import hybrid_search


def main() -> None:
    cases = [c for c in json.loads((Path(__file__).parent / "golden.json").read_text(encoding="utf-8"))
             if c["expect"] in ("answer", "refuse")]
    vecs = get_embedder().embed([c["question"] for c in cases], is_query=True)
    pos, neg = [], []
    with SessionLocal() as db:
        for c, v in zip(cases, vecs):
            hits = hybrid_search(db, c["question"], v, 5, 20)
            top = max((h.similarity for h in hits), default=0.0)
            (pos if c["expect"] == "answer" else neg).append((top, c["question"]))

    print(f"answerable:   min={min(s for s, _ in pos):.3f}  (n={len(pos)})")
    print(f"unanswerable: max={max(s for s, _ in neg):.3f}  (n={len(neg)})")
    best = None
    for t in [x / 100 for x in range(40, 80)]:
        tp = sum(s >= t for s, _ in pos) / len(pos)  # answerable kept
        tn = sum(s < t for s, _ in neg) / len(neg)   # unanswerable refused
        score = tp + tn
        if best is None or score > best[0] + 1e-9:
            best = (score, t, tp, tn)
    _, t, tp, tn = best
    print(f"best threshold={t:.2f}: keeps {tp:.0%} of answerable, refuses {tn:.0%} of unanswerable by retrieval alone")
    print("lowest answerable:", sorted(pos)[:3])
    print("highest unanswerable:", sorted(neg, reverse=True)[:3])


if __name__ == "__main__":
    main()
