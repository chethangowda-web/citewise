import time

import jwt
import pytest

from app.auth import hash_password, verify_password

DOC = b"# Refund Policy\n\nCustomers may request a full refund within 30 days of purchase.\n"
SECRET_DOC = b"# Salary Bands\n\nThe secret salary band for engineers is two hundred thousand dollars.\n"


@pytest.fixture
def auth_on(settings, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-" + "x" * 32)
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000)


def signup(client, email, password="correct-horse"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def upload(client, headers, name="refund_policy.md", body=DOC):
    return client.post("/api/documents", headers=headers, files={"file": (name, body, "text/markdown")})


def test_password_hash_roundtrip():
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", h)
    assert not verify_password("wrong", h)
    assert "hunter2" not in h
    assert not verify_password("x", "garbage")


def test_disabled_mode_is_open_and_register_404s(client):
    assert client.get("/api/auth/config").json()["auth_enabled"] is False
    assert client.get("/api/documents").status_code == 200
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "longenough"}).status_code == 404


def test_everything_requires_a_token_when_enabled(client, auth_on):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/sessions").status_code == 401
    assert client.post("/api/chat", json={"question": "hi"}).status_code == 401
    assert client.get("/api/documents", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_register_login_me(client, auth_on):
    h = signup(client, "Alice@Example.com")
    assert client.get("/api/auth/me", headers=h).json()["email"] == "alice@example.com"
    dup = client.post("/api/auth/register", json={"email": "alice@example.com", "password": "correct-horse"})
    assert dup.status_code == 422
    ok = client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": "correct-horse"})
    assert ok.status_code == 200 and ok.json()["token"]
    bad = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "nope-nope-nope"})
    ghost = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"})
    assert bad.status_code == ghost.status_code == 401 and bad.json() == ghost.json()  # no user enumeration


@pytest.mark.parametrize("email,password", [("not-an-email", "longenough"), ("a@b.co", "short")])
def test_registration_validation(client, auth_on, email, password):
    assert client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 422


def test_signup_can_be_closed(client, auth_on, settings, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "longenough"}).status_code == 403


def test_expired_forged_and_unsigned_tokens_are_rejected(client, auth_on, settings):
    exp = jwt.encode({"sub": "1", "exp": int(time.time()) - 5}, settings.jwt_secret, algorithm="HS256")
    forged = jwt.encode({"sub": "1", "exp": int(time.time()) + 600}, "other-secret-" + "y" * 32, algorithm="HS256")
    unsigned = jwt.encode({"sub": "1", "exp": int(time.time()) + 600}, "", algorithm="none")
    for t in (exp, forged, unsigned):
        assert client.get("/api/documents", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_login_is_rate_limited(client, auth_on, settings, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3)
    body = {"email": "a@b.co", "password": "wrongwrong"}
    codes = [client.post("/api/auth/login", json=body).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_private_documents_are_isolated(client, auth_on, llm):
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    r = upload(client, alice, "salary_bands.md", SECRET_DOC)
    assert r.json()["private"] is True
    doc_id = r.json()["id"]

    assert [d["title"] for d in client.get("/api/documents", headers=alice).json()] == ["Salary Bands"]
    assert client.get("/api/documents", headers=bob).json() == []

    # Bob cannot retrieve Alice's document, so the model never sees it
    q = {"question": "What is the secret salary band for engineers?"}
    res = client.post("/api/chat", headers=bob, json=q).json()
    assert res["outcome"] == "refused" and not llm.calls
    # ...but Alice can
    res = client.post("/api/chat", headers=alice, json=q).json()
    assert res["outcome"] == "answered" and res["sources"][0]["document"] == "Salary Bands"

    assert client.delete(f"/api/documents/{doc_id}", headers=bob).status_code == 404
    assert client.delete(f"/api/documents/{doc_id}", headers=alice).status_code == 204


def test_same_filename_for_two_users_does_not_collide(client, auth_on):
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    assert upload(client, alice).status_code == 200
    assert upload(client, bob).status_code == 200
    assert upload(client, alice).status_code == 200  # re-upload replaces her copy only
    assert len(client.get("/api/documents", headers=alice).json()) == 1
    assert len(client.get("/api/documents", headers=bob).json()) == 1


def test_shared_documents_are_visible_to_all_but_only_admin_deletes(client, auth_on, db):
    from app.rag.ingest import ingest_document

    shared = ingest_document(db, "handbook.md", "Handbook", "Employees get 25 vacation days per year.")
    signup(client, "boss@example.com")  # the first account is the admin; alice is an ordinary editor
    alice = signup(client, "alice@example.com")
    docs = client.get("/api/documents", headers=alice).json()
    assert [d["title"] for d in docs] == ["Handbook"] and docs[0]["private"] is False
    assert client.delete(f"/api/documents/{shared.id}", headers=alice).status_code == 403
    admin = {**alice, "X-Admin-Key": "test-admin"}
    assert client.delete(f"/api/documents/{shared.id}", headers=admin).status_code == 204


def test_history_is_saved_and_continues_one_session(client, auth_on, refund_doc_for):
    alice = signup(client, "alice@example.com")
    refund_doc_for(alice)
    first = client.post("/api/chat", headers=alice, json={"question": "How many days to request a refund?"}).json()
    sid = first["session_id"]
    assert sid
    second = client.post("/api/chat", headers=alice, json={"question": "Is the refund full?", "session_id": sid}).json()
    assert second["session_id"] == sid

    sessions = client.get("/api/sessions", headers=alice).json()
    assert [s["id"] for s in sessions] == [sid] and sessions[0]["title"].startswith("How many days")
    msgs = client.get(f"/api/sessions/{sid}", headers=alice).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[1]["meta"]["outcome"] == "answered" and msgs[1]["meta"]["sources"]


def test_sessions_are_private_to_their_owner(client, auth_on, refund_doc_for):
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    refund_doc_for(alice)
    q = {"question": "How many days to request a refund?"}
    sid = client.post("/api/chat", headers=alice, json=q).json()["session_id"]
    assert client.get("/api/sessions", headers=bob).json() == []
    assert client.get(f"/api/sessions/{sid}", headers=bob).status_code == 404
    assert client.delete(f"/api/sessions/{sid}", headers=bob).status_code == 404
    # Bob cannot append to Alice's conversation either
    assert client.post("/api/chat", headers=bob, json={"question": "hi", "session_id": sid}).status_code == 404
    assert client.delete(f"/api/sessions/{sid}", headers=alice).status_code == 204
    assert client.get(f"/api/sessions/{sid}", headers=alice).status_code == 404


def test_history_stores_redacted_question_not_raw_pii(client, refund_doc):
    q = {"question": "My SSN is 123-45-6789. How many days to request a refund?"}
    r = client.post("/api/chat", json=q).json()
    stored = client.get(f"/api/sessions/{r['session_id']}").json()["messages"][0]["content"]
    assert "123-45-6789" not in stored


def test_anonymous_mode_keeps_history_too(client, refund_doc):
    sid = client.post("/api/chat", json={"question": "How many days to request a refund?"}).json()["session_id"]
    assert [s["id"] for s in client.get("/api/sessions").json()] == [sid]
    assert client.get("/api/sessions/does-not-exist").status_code == 404
