import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models import Document, IngestJob
from app.rag import jobs
from app.rag.jobs import prune_jobs, recover_interrupted_jobs, run_ingest_job
from tests.conftest import fake_vector
from tests.test_auth import auth_on, signup  # noqa: F401  (auth_on is a fixture)

REFUND = b"# Refund Policy\n\nCustomers may request a full refund within 30 days of purchase.\n"
Q = {"question": "How many days to request a refund?"}


def upload(client, name="refund_policy.md", body=REFUND, headers=None, **form):
    return client.post("/api/documents", data=form, headers=headers or {}, files={"file": (name, body, "text/markdown")})


def background(client, name="refund_policy.md", body=REFUND, headers=None, **form):
    return upload(client, name, body, headers, background="true", **form)


def job(client, job_id, headers=None):
    return client.get(f"/api/documents/jobs/{job_id}", headers=headers or {})


def test_a_background_upload_is_accepted_at_once_and_then_filed(client, llm):
    r = background(client)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued" and len(body["job_id"]) == 32

    done = job(client, body["job_id"]).json()
    assert done["status"] == "done" and done["error"] == "" and done["filename"] == "refund_policy.md"
    assert done["result"]["title"] == "Refund Policy" and done["result"]["chunks"] >= 1 and done["result"]["version"] == 1
    out = client.post("/api/chat", json=Q).json()  # and it is really searchable
    assert out["outcome"] == "answered" and out["sources"][0]["document"] == "Refund Policy"


def test_a_background_upload_reports_exactly_what_a_direct_one_does(client):
    direct = upload(client, "a_policy.md", REFUND).json()
    queued = job(client, background(client, "b_policy.md", REFUND).json()["job_id"]).json()["result"]
    assert set(queued) == set(direct)
    assert {k: v for k, v in queued.items() if k not in ("id", "title")} == {k: v for k, v in direct.items() if k not in ("id", "title")}


def test_the_direct_path_is_unchanged(client):
    r = upload(client)
    assert r.status_code == 200 and r.json()["chunks"] >= 1


def test_an_unreadable_file_becomes_a_failed_job_with_the_reason(client):
    r = background(client, "broken.docx", b"definitely not a zip")
    assert r.status_code == 202  # accepted: the problem is only found when the file is opened
    j = job(client, r.json()["job_id"]).json()
    assert j["status"] == "failed" and "not a valid .docx" in j["error"] and j["result"] is None
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize(
    "name,body,expected",
    [("virus.exe", b"MZ", 400), ("big.md", b"x" * (5 * 1024 * 1024 + 1), 413)],
)
def test_checks_that_can_be_made_up_front_still_are(client, db, name, body, expected):
    assert background(client, name, body).status_code == expected
    assert db.query(IngestJob).count() == 0  # no job was created for a file that was never acceptable


def test_scope_rules_apply_before_a_job_is_created(client, auth_on, db):
    signup(client, "boss@example.com")
    alice = signup(client, "alice@example.com")
    assert background(client, headers=alice, scope="shared").status_code == 403  # only admins share with everyone
    assert background(client, headers=alice, scope="team", team_id="999").status_code == 403
    assert background(client, headers=alice, scope="bogus").status_code == 422
    assert db.query(IngestJob).count() == 0


def test_jobs_are_private_to_the_uploader(client, auth_on):
    signup(client, "boss@example.com")
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    jid = background(client, headers=alice).json()["job_id"]
    assert job(client, jid, alice).status_code == 200
    assert job(client, jid, bob).status_code == 404
    assert job(client, jid).status_code == 401
    assert job(client, "0" * 32, alice).status_code == 404


def test_team_scope_and_dates_and_versions_work_in_the_background(client, auth_on):
    boss = signup(client, "boss@example.com")
    alice = signup(client, "alice@example.com")
    tid = client.post("/api/admin/teams", headers=boss, json={"name": "HR"}).json()["id"]
    uid = next(u["id"] for u in client.get("/api/admin/users", headers=boss).json() if u["email"] == "alice@example.com")
    client.put(f"/api/admin/teams/{tid}/members/{uid}", headers=boss)

    first = job(client, background(client, headers=alice, scope="team", team_id=str(tid), effective_date="2026-02-01").json()["job_id"], alice).json()
    assert first["result"]["scope"] == "team" and first["result"]["version"] == 1
    changed = REFUND.replace(b"30", b"14")
    second = job(client, background(client, body=changed, headers=alice, scope="team", team_id=str(tid)).json()["job_id"], alice).json()
    assert second["result"]["version"] == 2
    same = job(client, background(client, body=changed, headers=alice, scope="team", team_id=str(tid)).json()["job_id"], alice).json()
    assert same["result"]["unchanged"] is True and same["result"]["version"] == 2
    assert [d["version"] for d in client.get("/api/documents", headers=alice).json()] == [2]


def test_an_unexpected_crash_fails_the_job_without_leaking_details(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internal detail: db password is hunter2")

    monkeypatch.setattr(jobs, "ingest_document", boom)
    j = job(client, background(client).json()["job_id"]).json()
    assert j["status"] == "failed" and "Something went wrong" in j["error"]
    assert "hunter2" not in j["error"] and "secret" not in j["error"]


def test_only_a_few_documents_are_embedded_at_a_time(client, db, settings, monkeypatch):
    monkeypatch.setattr(settings, "ingest_concurrency", 2)
    active = peak = 0
    lock = threading.Lock()

    class Slow:
        def embed(self, texts, is_query=False):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.25)
            with lock:
                active -= 1
            return [fake_vector(t) for t in texts]

    monkeypatch.setattr("app.rag.ingest.get_embedder", lambda: Slow())
    ids = []
    for i in range(6):
        j = IngestJob(id=f"{i:032d}", filename=f"doc{i}.md")
        db.add(j)
        ids.append(j.id)
    db.commit()
    threads = [
        threading.Thread(target=run_ingest_job, args=(jid, f"doc{i}.md", f"# Doc {i}\n\nPolicy number {i} says hello.\n".encode(), None, None, None))
        for i, jid in enumerate(ids)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert peak <= 2, peak  # six uploads, never more than two in flight
    assert peak == 2  # ...and they really did overlap
    db.expire_all()
    assert [j.status for j in db.query(IngestJob).order_by(IngestJob.id)] == ["done"] * 6
    assert db.query(Document).count() == 6


def test_jobs_interrupted_by_a_restart_are_marked_failed(client, db):
    now = datetime.now(timezone.utc)
    db.add_all([
        IngestJob(id="a" * 32, filename="q.md", status="queued"),
        IngestJob(id="b" * 32, filename="r.md", status="running"),
        IngestJob(id="c" * 32, filename="d.md", status="done", result={"id": 1}, finished_at=now),
    ])
    db.commit()
    assert recover_interrupted_jobs() == 2
    db.expire_all()
    status = {j.filename: (j.status, j.error) for j in db.query(IngestJob)}
    assert status["q.md"][0] == status["r.md"][0] == "failed" and "restarted" in status["q.md"][1]
    assert status["d.md"] == ("done", "")  # finished work is left alone


def test_old_finished_jobs_are_pruned_but_recent_and_unfinished_ones_stay(client, db):
    now = datetime.now(timezone.utc)
    db.add_all([
        IngestJob(id="1" * 32, filename="old.md", status="done", finished_at=now - timedelta(days=8)),
        IngestJob(id="2" * 32, filename="recent.md", status="done", finished_at=now - timedelta(days=1)),
        IngestJob(id="3" * 32, filename="working.md", status="running"),
    ])
    db.commit()
    prune_jobs(db)
    assert sorted(j.filename for j in db.query(IngestJob)) == ["recent.md", "working.md"]


def test_deleting_an_account_removes_its_jobs(client, auth_on, db):
    signup(client, "boss@example.com")
    alice = signup(client, "alice@example.com")
    background(client, headers=alice)
    r = client.request("DELETE", "/api/auth/me", json={"password": "correct-horse"}, headers=alice)
    assert r.status_code == 200
    assert db.scalar(text("select count(*) from ingest_jobs")) == 0
