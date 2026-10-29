from app.rag.ingest import ingest_document
from app.rag.retriever import (
    Retrieved,
    fuse,
    hybrid_search,
    keyword_search,
    rerank_hits,
    vector_search,
)
from tests.conftest import fake_vector

FLIGHTS = "Flights must be booked at least 14 days in advance in economy class."
HOTELS = "Hotel stays are reimbursed up to 180 USD per night."
QUESTION = "How far in advance must flights be booked and which class?"


def _load(db):
    ingest_document(db, "travel.md", "Travel", FLIGHTS)
    ingest_document(db, "hotels.md", "Hotels", HOTELS)


def _hit(i, score=0.0, sim=0.5):
    return Retrieved(i, "t", f"s{i}.md", f"text {i}", sim, score)


def test_and_mode_misses_a_natural_language_question_but_or_mode_finds_it(db):
    _load(db)
    qvec = fake_vector(QUESTION)
    assert keyword_search(db, QUESTION, qvec, 5, mode="and") == []  # "how far ..." words are not all present
    hits = keyword_search(db, QUESTION, qvec, 5, mode="or")
    assert hits and hits[0].source == "travel.md"


def test_keyword_hits_carry_cosine_similarity(db):
    _load(db)
    hits = keyword_search(db, QUESTION, fake_vector(QUESTION), 5, mode="or")
    assert all(0.0 <= h.similarity <= 1.0 for h in hits) and hits[0].similarity > 0


def test_keyword_search_with_only_stopwords_returns_nothing(db):
    _load(db)
    assert keyword_search(db, "how is it", fake_vector("how is it"), 5, mode="or") == []


def test_vector_search_orders_by_similarity(db):
    _load(db)
    hits = vector_search(db, fake_vector(QUESTION), 5)
    assert hits[0].source == "travel.md" and hits[0].similarity >= hits[1].similarity


def test_fuse_rewards_agreement_between_lists():
    a = [_hit(1), _hit(2), _hit(3)]
    b = [_hit(3), _hit(1)]
    fused = fuse(a, b)
    assert [h.chunk_id for h in fused][:2] == [1, 3]  # 1 and 3 appear in both lists; 2 only in one
    assert fused[0].score > fused[-1].score


def test_fuse_does_not_mutate_its_inputs():
    a = [_hit(1)]
    fuse(a, [_hit(1)])
    assert a[0].score == 0.0


def test_rerank_hits_reorders_by_the_reranker_score():
    class Fake:
        def score(self, query, passages):
            return [1.0 if "text 3" in p else 0.0 for p in passages]

    out = rerank_hits("q", [_hit(1), _hit(2), _hit(3)], Fake())
    assert out[0].chunk_id == 3 and out[0].score == 1.0


def test_hybrid_search_applies_reranker_and_top_k(db):
    _load(db)

    class PreferHotels:
        def score(self, query, passages):
            return [1.0 if "Hotel" in p else 0.0 for p in passages]

    hits = hybrid_search(db, QUESTION, fake_vector(QUESTION), 1, 10, rerank=True, reranker=PreferHotels())
    assert len(hits) == 1 and hits[0].source == "hotels.md"  # the reranker overrode the fused order
    plain = hybrid_search(db, QUESTION, fake_vector(QUESTION), 1, 10, rerank=False)
    assert plain[0].source == "travel.md"
