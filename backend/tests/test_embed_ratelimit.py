import pytest
from google.genai import errors

from app.config import get_settings
from app.llm import providers
from app.llm.providers import GeminiClient, LLMUnavailable, _rate_limit_delay


def limited(retry_in="31.4s", code=429):
    msg = f"Embed content requests per minute limit exceeded on Free Tier. Please retry in {retry_in}." if retry_in else "Quota exceeded."
    return errors.ClientError(code, {"error": {"code": code, "message": msg, "status": "RESOURCE_EXHAUSTED"}})


class _Vec:
    def __init__(self, n):
        self.values = [float(n), 1.0, 0.0]


class _Models:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def embed_content(self, model, contents, config):
        self.calls.append(len(contents))
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        return type("Res", (), {"embeddings": [_Vec(i) for i in range(len(contents))]})()


def make_client(script):
    c = GeminiClient.__new__(GeminiClient)
    c._s = get_settings()
    c._client = type("Fake", (), {"models": _Models(script)})()
    return c


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(providers.time, "sleep", lambda s: slept.append(s))
    return slept


def test_a_rate_limit_is_waited_out_as_long_as_the_api_asks(sleeps):
    c = make_client([limited("31.4s"), limited("12s")])
    out = c.embed(["a", "b", "c"])
    assert len(out) == 3 and sleeps == [32.4, 13.0]  # the API's own hint plus a second
    assert c._client.models.calls == [3, 3, 3]  # the same batch, retried until it went through


def test_work_already_done_is_not_repeated_when_a_later_batch_is_limited(sleeps):
    texts = [f"t{i}" for i in range(150)]  # two batches: 100 and 50
    c = make_client([None, limited("5s"), None])
    assert len(c.embed(texts)) == 150
    assert c._client.models.calls == [100, 50, 50]  # batch one was not sent again
    assert sleeps == [6.0]


def test_it_gives_up_after_the_configured_patience_with_a_plain_message(sleeps, monkeypatch):
    monkeypatch.setattr(get_settings(), "embed_rate_limit_retries", 3)
    c = make_client([limited("10s")] * 10)
    with pytest.raises(LLMUnavailable, match="rate-limited"):
        c.embed(["a"])
    assert len(sleeps) == 3 and len(c._client.models.calls) == 4  # one try plus three waits


def test_other_client_errors_are_not_retried(sleeps):
    c = make_client([errors.ClientError(400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}})])
    with pytest.raises(errors.ClientError):
        c.embed(["a"])
    assert sleeps == [] and c._client.models.calls == [1]


def test_a_server_error_gets_two_quick_retries_then_surfaces(sleeps):
    boom = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    c = make_client([boom, boom, boom, None])
    with pytest.raises(errors.ServerError):
        c.embed(["a"])
    assert len(c._client.models.calls) == 3 and sleeps == [2, 4]
    ok = make_client([boom, None])
    assert len(ok.embed(["a"])) == 1


@pytest.mark.parametrize(
    "retry_in,expected",
    [("31.4s", 32.4), ("0.2s", 2.0), ("300s", 65.0), (None, 30.0)],  # floor, cap, and no hint at all
)
def test_the_wait_is_sensible(retry_in, expected):
    assert _rate_limit_delay(limited(retry_in)) == expected


def test_only_429_counts_as_a_rate_limit():
    assert _rate_limit_delay(limited("5s", code=400)) is None
    assert _rate_limit_delay(limited("5s", code=403)) is None


def test_the_normalised_vectors_are_returned_in_order(sleeps):
    out = make_client([]).embed(["a", "b"])
    assert len(out) == 2 and abs(sum(v * v for v in out[1]) - 1.0) < 1e-9  # unit length, as search expects


# ---------- what a person sees ----------

def test_a_background_job_explains_a_rate_limit_that_would_not_clear(client, monkeypatch):
    class Limited:
        def embed(self, texts, is_query=False):
            raise LLMUnavailable("The embedding service is rate-limited right now. Please try again in a few minutes.")

    monkeypatch.setattr("app.rag.ingest.get_embedder", lambda: Limited())
    r = client.post("/api/documents", data={"background": "true"}, files={"file": ("a.md", b"# A\n\nSome policy text here.", "text/markdown")})
    job = client.get(f"/api/documents/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and "rate-limited" in job["error"] and "Traceback" not in job["error"]


def test_a_direct_upload_gets_a_503_not_a_crash(client, monkeypatch):
    class Limited:
        def embed(self, texts, is_query=False):
            raise LLMUnavailable("The embedding service is rate-limited right now. Please try again in a few minutes.")

    monkeypatch.setattr("app.rag.ingest.get_embedder", lambda: Limited())
    r = client.post("/api/documents", files={"file": ("a.md", b"# A\n\nSome policy text here.", "text/markdown")})
    assert r.status_code == 503 and "rate-limited" in r.json()["detail"]
    assert client.get("/api/documents").json() == []  # nothing half-filed
