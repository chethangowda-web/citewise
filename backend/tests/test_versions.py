import pytest
from sqlalchemy import text

ADMIN = {"X-Admin-Key": "test-admin"}
V1 = b"# Refund Policy\n\nCustomers may request a full refund within 30 days of purchase.\n"
V2 = b"# Refund Policy\n\nCustomers may request a full refund within 14 days of purchase.\n"
Q = {"question": "How many days to request a refund?"}


def put(client, body=V1, name="refund_policy.md", **form):
    return client.post("/api/documents", data=form, files={"file": (name, body, "text/markdown")})


def listing(client):
    return client.get("/api/documents").json()


def all_rows(client):
    return client.get("/api/admin/documents", headers=ADMIN).json()


def test_first_upload_is_version_one(client):
    r = put(client).json()
    assert r["version"] == 1 and r["unchanged"] is False
    assert [(d["version"], d["versions"]) for d in listing(client)] == [(1, 1)]


def test_reuploading_identical_text_does_nothing_and_does_not_call_the_embedder(client, monkeypatch):
    put(client)
    calls = []

    class Counting:
        def embed(self, texts, is_query=False):
            calls.append(len(texts))
            return [[0.0] * 768 for _ in texts]

    monkeypatch.setattr("app.rag.ingest.get_embedder", lambda: Counting())
    r = put(client).json()
    assert r["unchanged"] is True and r["version"] == 1
    assert calls == []  # no tokens spent re-embedding a file that did not change
    assert len(all_rows(client)) == 1


def test_changed_text_keeps_the_old_version_as_superseded(client):
    put(client)
    r = put(client, V2).json()
    assert r["version"] == 2 and r["unchanged"] is False
    docs = listing(client)
    assert [(d["version"], d["versions"]) for d in docs] == [(2, 2)]  # users only see the current one
    rows = {d["version"]: d["status"] for d in all_rows(client)}
    assert rows == {1: "superseded", 2: "current"}  # the admin sees the whole history


def test_answers_use_only_the_current_version(client, llm):
    put(client)
    put(client, V2)
    client.post("/api/chat", json=Q)
    prompt = llm.calls[-1][1]
    assert "14 days" in prompt and "30 days" not in prompt


def test_restoring_an_old_version_swaps_it_back_in(client, llm):
    put(client)
    put(client, V2)
    v1 = next(d["id"] for d in all_rows(client) if d["version"] == 1)
    r = client.post(f"/api/admin/documents/{v1}/restore", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "current"
    assert {d["version"]: d["status"] for d in all_rows(client)} == {1: "current", 2: "superseded"}
    client.post("/api/chat", json=Q)
    assert "30 days" in llm.calls[-1][1] and "14 days" not in llm.calls[-1][1]
    assert client.post(f"/api/admin/documents/{v1}/restore", headers=ADMIN).status_code == 409  # already current
    assert client.post("/api/admin/documents/9999/restore", headers=ADMIN).status_code == 404
    assert put(client, V2).json()["version"] == 3  # numbering keeps counting up after a restore


def test_restore_is_for_admins_only(client, settings, monkeypatch):
    put(client)
    put(client, V2)
    old = next(d["id"] for d in all_rows(client) if d["version"] == 1)
    assert client.post(f"/api/admin/documents/{old}/restore").status_code == 401
    assert client.post(f"/api/admin/documents/{old}/restore", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_deleting_a_document_removes_every_version_and_its_passages(client, db):
    put(client)
    put(client, V2)
    doc = listing(client)[0]["id"]
    assert client.delete(f"/api/documents/{doc}", headers=ADMIN).status_code == 204
    assert all_rows(client) == []
    assert db.scalar(text("select count(*) from chunks")) == 0  # nothing of the old text lingers


def test_effective_date_is_recorded_shown_and_validated(client):
    r = put(client, effective_date="2026-01-15").json()
    assert r["version"] == 1
    assert listing(client)[0]["updated_at"] == "2026-01-15"
    assert put(client, V2, effective_date="not-a-date").status_code == 422
    # same text with a corrected date is not a new version, just a corrected date
    again = put(client, effective_date="2026-02-01").json()
    assert again["unchanged"] is True and again["version"] == 1
    assert listing(client)[0]["updated_at"] == "2026-02-01"


def test_answers_citing_an_old_document_carry_an_outdated_warning(client):
    put(client, effective_date="2020-01-01")
    r = client.post("/api/chat", json=Q).json()
    assert r["outcome"] == "answered"  # still answered: stale is a warning, not a refusal
    assert "outdated_source" in r["flags"]
    src = r["sources"][0]
    assert src["stale"] is True and src["updated_at"] == "2020-01-01" and src["version"] == 1
    assert listing(client)[0]["stale"] is True


def test_recent_documents_are_not_flagged(client):
    put(client)
    r = client.post("/api/chat", json=Q).json()
    assert "outdated_source" not in r["flags"] and r["sources"][0]["stale"] is False


def test_the_warning_can_be_switched_off(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "stale_after_days", 0)
    put(client, effective_date="2020-01-01")
    r = client.post("/api/chat", json=Q).json()
    assert "outdated_source" not in r["flags"] and r["sources"][0]["stale"] is False


def test_a_restored_old_version_shows_its_own_date(client):
    put(client, effective_date="2020-01-01")
    put(client, V2, effective_date="2026-06-01")
    assert "outdated_source" not in client.post("/api/chat", json=Q).json()["flags"]  # v2 is fresh
    old = next(d["id"] for d in all_rows(client) if d["version"] == 1)
    client.post(f"/api/admin/documents/{old}/restore", headers=ADMIN)
    assert "outdated_source" in client.post("/api/chat", json=Q).json()["flags"]  # v1 is old again
