"""Integration-test fixtures: real FastAPI app + real Postgres/pgvector, fake LLM and embeddings.

Needs a pgvector Postgres reachable at TEST_DATABASE_URL (default: the docker-compose db on :5440).
The `rag_test` database is created on demand, so the dev database is never touched.
"""
import hashlib
import os
import re

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://rag:rag@localhost:5440/rag_test")
os.environ.update(
    DATABASE_URL=TEST_DB_URL,
    MIN_RELEVANCE="0.05",  # fake hashing embeddings are far less similar than real ones
    RATE_LIMIT_PER_MINUTE="1000",
    API_KEY="",
    ADMIN_API_KEY="test-admin",
    GUARDRAILS_ENABLED="true",
    LLM_PROVIDER="groq",
    GEMINI_API_KEY="fake",
    GROQ_API_KEY="fake",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm.providers import Generation  # noqa: E402


def _ensure_database() -> None:
    url = make_url(TEST_DB_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}):
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


_ensure_database()

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.observability import limiter  # noqa: E402


def fake_vector(text_: str, dim: int = 768) -> list[float]:
    """Deterministic bag-of-words hashing embedding: shared words => similar vectors."""
    v = [0.0] * dim
    for w in re.findall(r"[a-z0-9]+", text_.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


class FakeEmbedder:
    def embed(self, texts, is_query=False):
        return [fake_vector(t) for t in texts]


class FakeLLM:
    """Scriptable generator; records every prompt it receives.

    `queue` holds one scripted reply (or an Exception to raise) per call, consumed in order; when it is
    empty every call returns `reply`. That lets a test script the rewrite call and the answer call apart.
    """

    def __init__(self):
        self.reply = "The policy says so [1]."
        self.error: Exception | None = None
        self.queue: list = []
        self.calls: list[tuple[str, str]] = []

    def _next(self) -> str:
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.error:
            raise self.error
        return self.reply

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        return Generation(self._next(), "fake-model", 120, 25)

    def stream(self, system, prompt):
        self.calls.append((system, prompt))
        text = self._next()
        words = text.split(" ")
        for i, w in enumerate(words):
            yield w + (" " if i < len(words) - 1 else "")
        yield Generation(text, "fake-model", 120, 25)


@pytest.fixture(autouse=True)
def _wire_fakes(monkeypatch):
    llm = FakeLLM()
    embedder = FakeEmbedder()
    monkeypatch.setattr("app.rag.pipeline.get_embedder", lambda: embedder)
    monkeypatch.setattr("app.rag.pipeline.get_generator", lambda: llm)
    monkeypatch.setattr("app.rag.ingest.get_embedder", lambda: embedder)
    return llm


@pytest.fixture
def llm(_wire_fakes) -> FakeLLM:
    return _wire_fakes


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # runs lifespan -> init_db (extension, tables, migration)
        yield c


@pytest.fixture(autouse=True)
def _clean_state(client):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE chunks, documents, audit_logs, chat_messages, chat_sessions, team_members, teams, password_resets, login_failures, escalations, ingest_jobs, users RESTART IDENTITY CASCADE"))
    limiter.reset()
    client.cookies.clear()  # a cookie session from one test must not authenticate the next
    yield


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def refund_doc(client):
    body = b"# Refund Policy\n\nCustomers may request a full refund within 30 days of purchase.\n"
    r = client.post("/api/documents", files={"file": ("refund_policy.md", body, "text/markdown")})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def refund_doc_for(client):
    def _upload(headers):
        body = b"# Refund Policy\n\nCustomers may request a full refund within 30 days of purchase.\n"
        r = client.post("/api/documents", headers=headers, files={"file": ("refund_policy.md", body, "text/markdown")})
        assert r.status_code == 200, r.text
        return r.json()

    return _upload
