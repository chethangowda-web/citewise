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


# ---------- resumable collection ----------
def _cases(n=4):
    return [{"id": f"q{i}", "question": f"question {i}"} for i in range(n)]


def _ok(q):
    return {"answer": f"ans {q}", "flags": [], "sources": []}


UNAVAILABLE = {"answer": "", "flags": ["eval_api_unavailable"], "sources": []}


def test_collect_completes_and_saves_checkpoint(tmp_path):
    from eval.run import collect

    ckpt = tmp_path / "c.json"
    raw, paused = collect(_cases(), _ok, ckpt)
    assert not paused and sorted(raw) == ["q0", "q1", "q2", "q3"]


def test_collect_pauses_after_consecutive_failures_and_never_stores_them(tmp_path):
    from eval.run import collect

    calls = iter([_ok("a"), _ok("b"), UNAVAILABLE, UNAVAILABLE, UNAVAILABLE])
    raw, paused = collect(_cases(6), lambda q: next(calls), tmp_path / "c.json", max_unavailable=3)
    assert paused and sorted(raw) == ["q0", "q1"]  # the failed ones are absent, not recorded as wrong answers


def test_collect_resumes_without_re_asking_finished_cases(tmp_path):
    import json

    from eval.run import collect

    ckpt = tmp_path / "c.json"
    ckpt.write_text(json.dumps({"q0": _ok("a"), "q1": _ok("b")}), encoding="utf-8")
    asked = []
    raw, paused = collect(_cases(4), lambda q: asked.append(q) or _ok(q), ckpt)
    assert not paused and asked == ["question 2", "question 3"] and len(raw) == 4


def test_collect_isolated_failure_does_not_pause(tmp_path):
    from eval.run import collect

    seq = iter([_ok("a"), UNAVAILABLE, _ok("b"), _ok("c")])
    raw, paused = collect(_cases(4), lambda q: next(seq), None, max_unavailable=3)
    assert sorted(raw) == ["q0", "q2", "q3"] and paused  # q1 was skipped, so the run is not complete


def test_report_printing_survives_a_legacy_console_encoding():
    """Regression: a guardrails-off answer with U+2011 crashed the run on a cp1252 console."""
    import os
    import subprocess
    import sys

    code = "from eval.run import utf8_output; utf8_output(); print('30\u2011day window \u3010\u0031\u3011')"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "cp1252"}, cwd="."
    )
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    assert "day window" in out.stdout.decode("utf-8")
