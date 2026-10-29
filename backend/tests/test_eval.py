from eval.run import grade, summarize

ANSWER_CASE = {"id": "q1", "category": "answerable", "question": "q", "expect": "answer",
               "keywords": ["6|six"], "source": "a.md"}


def res(answer="", flags=None, sources=None):
    return {"answer": answer, "flags": flags or [], "sources": [{"source": s} for s in (sources or [])]}


def test_grade_answer_accepts_alternative_keywords():
    assert grade(ANSWER_CASE, res("After six months [1].", sources=["a.md"]))["passed"]
    assert grade(ANSWER_CASE, res("After 6 months [1].", sources=["a.md"]))["passed"]


def test_grade_answer_fails_wrong_source_or_refusal():
    assert not grade(ANSWER_CASE, res("six", sources=["b.md"]))["passed"]
    assert not grade(ANSWER_CASE, res("six", flags=["ungrounded_answer"], sources=["a.md"]))["passed"]


def test_grade_refuse_and_block():
    refuse = {"id": "q2", "category": "unanswerable_offtopic", "question": "q", "expect": "refuse"}
    assert grade(refuse, res("no", flags=["no_relevant_context"]))["passed"]
    assert not grade(refuse, res("Paris [1]", sources=["a.md"]))["passed"]
    block = {"id": "q3", "category": "injection", "question": "q", "expect": "block"}
    assert grade(block, res("no", flags=["prompt_injection"]))["passed"]
    assert not grade(block, res("sure"))["passed"]


def test_summarize_metrics():
    results = [
        grade(ANSWER_CASE, res("six", sources=["a.md"])),
        grade(ANSWER_CASE, res("n/a", flags=["no_relevant_context"])),
    ]
    m = summarize(results)
    assert m["answer_accuracy"] == 0.5 and m["false_refusal_rate"] == 0.5


def test_norm_treats_spelled_numbers_as_digits():
    from eval.run import norm

    assert "3" in norm("up to three days per week") and "16" in norm("Sixteen weeks")
    assert "someone" in norm("someone")  # word boundaries: 'one' inside a word is untouched
