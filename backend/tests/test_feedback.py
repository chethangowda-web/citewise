import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import AuditLog, ChatSession, Escalation
from tests.test_auth import auth_on, signup  # noqa: F401  (auth_on is a fixture)

ADMIN = {"X-Admin-Key": "test-admin"}
Q = {"question": "How many days to request a refund?"}


def erase(client, headers, body):
    return client.request("DELETE", "/api/auth/me", json=body, headers=headers)


def ask(client, headers=None, **extra):
    r = client.post("/api/chat", json={**Q, **extra}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def answered(client, refund_doc):
    return ask(client)


# ---------- feedback ----------

def test_chat_responses_carry_the_answer_id(client, answered):
    assert answered["message_id"] > 0
    msgs = client.get(f"/api/sessions/{answered['session_id']}").json()["messages"]
    assert msgs[1]["id"] == answered["message_id"] and msgs[1]["feedback"] is None


def test_thumbs_up_down_and_changing_your_mind(client, answered):
    mid = answered["message_id"]
    assert client.post(f"/api/messages/{mid}/feedback", json={"rating": "up"}).json()["rating"] == "up"
    saved = lambda: client.get(f"/api/sessions/{answered['session_id']}").json()["messages"][1]["feedback"]  # noqa: E731
    assert saved() == 1
    client.post(f"/api/messages/{mid}/feedback", json={"rating": "down", "reason": "This is out of date"})
    assert saved() == -1
    client.post(f"/api/messages/{mid}/feedback", json={"rating": "none"})
    assert saved() is None


@pytest.mark.parametrize("body", [{"rating": "sideways"}, {"rating": "down", "reason": "x" * 501}, {}])
def test_bad_feedback_is_rejected(client, answered, body):
    assert client.post(f"/api/messages/{answered['message_id']}/feedback", json=body).status_code == 422


def test_feedback_reasons_are_stripped_of_personal_data(client, answered):
    mid = answered["message_id"]
    client.post(f"/api/messages/{mid}/feedback", json={"rating": "down", "reason": "Wrong, mail me at bob@example.com or 123-45-6789"})
    row = client.get("/api/admin/feedback", headers=ADMIN).json()["rows"][0]
    assert "bob@example.com" not in row["reason"] and "123-45-6789" not in row["reason"]
    assert "[EMAIL_REDACTED]" in row["reason"]


def test_you_can_only_rate_your_own_answers(client, auth_on, refund_doc_for):
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    refund_doc_for(alice)
    mid = ask(client, alice)["message_id"]
    assert client.post(f"/api/messages/{mid}/feedback", json={"rating": "up"}, headers=bob).status_code == 404
    assert client.post("/api/messages/99999/feedback", json={"rating": "up"}, headers=alice).status_code == 404
    assert client.post(f"/api/messages/{mid}/feedback", json={"rating": "up"}).status_code == 401
    assert client.post(f"/api/messages/{mid}/feedback", json={"rating": "up"}, headers=alice).status_code == 200


def test_the_review_queue_shows_what_went_wrong(client, answered):
    client.post(f"/api/messages/{answered['message_id']}/feedback", json={"rating": "down", "reason": "Wrong number of days"})
    ok = ask(client, session_id=answered["session_id"])
    client.post(f"/api/messages/{ok['message_id']}/feedback", json={"rating": "up"})

    down = client.get("/api/admin/feedback", headers=ADMIN).json()
    assert (down["up"], down["down"]) == (1, 1) and len(down["rows"]) == 1
    row = down["rows"][0]
    assert row["rating"] == "down" and row["reason"] == "Wrong number of days"
    assert row["question"] == Q["question"] and row["sources"] == ["Refund Policy"] and row["outcome"] == "answered"
    assert len(client.get("/api/admin/feedback?rating=all", headers=ADMIN).json()["rows"]) == 2
    assert client.get("/api/admin/feedback").status_code in (401, 404)  # admins only


# ---------- asking a person ----------

def test_a_question_can_be_sent_to_staff_once(client, refund_doc, llm):
    llm.reply = "I don't know based on the provided documents."  # no citation: the answer is refused
    refused = ask(client, question="What is the capital of France?")
    assert refused["outcome"] in ("refused", "blocked")
    body = {"message_id": refused["message_id"], "note": "Please check with legal. Call 555-123-4567."}
    r = client.post("/api/escalations", json=body)
    assert r.status_code == 201 and r.json()["status"] == "open" and r.json()["already_sent"] is False
    assert "555-123-4567" not in r.json()["note"]
    again = client.post("/api/escalations", json=body).json()
    assert again["already_sent"] is True and again["id"] == r.json()["id"]  # no duplicates from a double click
    mine = client.get("/api/escalations").json()
    assert [(e["id"], e["status"]) for e in mine] == [(r.json()["id"], "open")]
    assert mine[0]["question"] == "What is the capital of France?"


def test_staff_inbox_and_the_emailed_reply(client, auth_on, refund_doc_for, monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.admin_ops.send_mail", lambda to, subject, body: sent.append((to, subject, body)))
    alice = signup(client, "alice@example.com")
    refund_doc_for(alice)
    mid = ask(client, alice, question="Can I get a refund after a year?")["message_id"]
    esc = client.post("/api/escalations", json={"message_id": mid, "note": "urgent"}, headers=alice).json()

    inbox = client.get("/api/admin/escalations", headers=ADMIN).json()
    assert [(e["id"], e["user"], e["note"]) for e in inbox] == [(esc["id"], "alice@example.com", "urgent")]

    r = client.post(f"/api/admin/escalations/{esc['id']}/resolve", headers=ADMIN, json={"reply": "Yes, within 60 days with a receipt."})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert [(to, "answered" in subject) for to, subject, _ in sent] == [("alice@example.com", True)]
    assert "within 60 days" in sent[0][2] and "Can I get a refund after a year?" in sent[0][2]
    mine = client.get("/api/escalations", headers=alice).json()
    assert mine[0]["status"] == "resolved" and mine[0]["reply"] == "Yes, within 60 days with a receipt."
    assert client.get("/api/admin/escalations", headers=ADMIN).json() == []  # the open inbox is empty again
    assert len(client.get("/api/admin/escalations?status=all", headers=ADMIN).json()) == 1
    assert client.post(f"/api/admin/escalations/{esc['id']}/resolve", headers=ADMIN, json={"reply": "again"}).status_code == 409
    assert client.post("/api/admin/escalations/9999/resolve", headers=ADMIN, json={"reply": "x"}).status_code == 404
    assert client.post(f"/api/admin/escalations/{esc['id']}/resolve", headers=ADMIN, json={"reply": ""}).status_code == 422


def test_escalations_are_private_to_the_asker(client, auth_on, refund_doc_for):
    signup(client, "boss@example.com")  # the first account is the admin
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    refund_doc_for(alice)
    mid = ask(client, alice)["message_id"]
    assert client.post("/api/escalations", json={"message_id": mid}, headers=bob).status_code == 404
    client.post("/api/escalations", json={"message_id": mid}, headers=alice)
    assert client.get("/api/escalations", headers=bob).json() == []
    assert client.get("/api/admin/escalations", headers=alice).status_code == 403  # editors cannot read the inbox


# ---------- audit export ----------

def test_audit_export_is_a_csv_with_the_ledger(client, refund_doc):
    ask(client)
    r = client.get("/api/admin/logs/export", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1 and rows[0]["outcome"] == "answered" and rows[0]["question"] == Q["question"]
    assert rows[0]["sources"] == "Refund Policy".replace("Refund Policy", "refund_policy.md")
    assert client.get("/api/admin/logs/export?outcome=blocked", headers=ADMIN).text.count("\n") == 1  # header only
    assert client.get("/api/admin/logs/export").status_code in (401, 404)


def test_audit_export_defuses_spreadsheet_formulas(client, db):
    db.add(AuditLog(question="=HYPERLINK(\"http://evil\",\"x\")", answer="+1+1", flags=[], sources=[]))
    db.add(AuditLog(question="@SUM(A1)", answer="-2+3", flags=[], sources=[]))
    db.commit()
    rows = list(csv.DictReader(io.StringIO(client.get("/api/admin/logs/export", headers=ADMIN).text)))
    cells = [r["question"] for r in rows] + [r["answer"] for r in rows]
    assert all(c.startswith("'") for c in cells), cells  # Excel would otherwise run these


def test_audit_export_can_be_limited_to_recent_days(client, db):
    db.add(AuditLog(question="old", answer="a", flags=[], sources=[], created_at=datetime.now(timezone.utc) - timedelta(days=40)))
    db.add(AuditLog(question="new", answer="a", flags=[], sources=[]))
    db.commit()
    assert [r["question"] for r in csv.DictReader(io.StringIO(client.get("/api/admin/logs/export?days=7", headers=ADMIN).text))] == ["new"]
    assert len(list(csv.DictReader(io.StringIO(client.get("/api/admin/logs/export", headers=ADMIN).text)))) == 2


# ---------- retention ----------

def test_retention_deletes_old_conversations_and_audit_rows(client, refund_doc, db, settings, monkeypatch):
    monkeypatch.setattr(settings, "retention_days", 30)
    monkeypatch.setattr(settings, "audit_retention_days", 90)
    old = ask(client)["session_id"]
    fresh = ask(client)["session_id"]
    db.get(ChatSession, old).updated_at = datetime.now(timezone.utc) - timedelta(days=31)
    db.query(AuditLog).update({AuditLog.created_at: datetime.now(timezone.utc) - timedelta(days=100)})
    db.commit()

    assert client.post("/api/admin/retention/run", headers=ADMIN).json() == {"sessions": 1, "audit": 2}
    ids = [s["id"] for s in client.get("/api/sessions").json()]
    assert ids == [fresh]
    assert client.get(f"/api/sessions/{old}").status_code == 404
    from sqlalchemy import text

    assert db.scalar(text("select count(*) from chat_messages")) == 2  # the old conversation's messages went too


def test_retention_is_off_by_default(client, refund_doc, db):
    sid = ask(client)["session_id"]
    db.get(ChatSession, sid).updated_at = datetime.now(timezone.utc) - timedelta(days=5000)
    db.commit()
    assert client.post("/api/admin/retention/run", headers=ADMIN).json() == {"sessions": 0, "audit": 0}
    assert client.get(f"/api/sessions/{sid}").status_code == 200


def test_escalations_survive_their_conversation_being_purged(client, refund_doc, db, settings, monkeypatch):
    monkeypatch.setattr(settings, "retention_days", 1)
    mid = ask(client)["message_id"]
    client.post("/api/escalations", json={"message_id": mid})
    db.query(ChatSession).update({ChatSession.updated_at: datetime.now(timezone.utc) - timedelta(days=5)})
    db.commit()
    client.post("/api/admin/retention/run", headers=ADMIN)
    db.expire_all()
    e = db.query(Escalation).one()
    assert e.session_id is None and e.question == Q["question"]  # staff still see what was asked


# ---------- your data: export and erasure ----------

def test_a_user_can_download_all_their_data(client, auth_on, refund_doc_for):
    alice = signup(client, "alice@example.com")
    refund_doc_for(alice)
    a = ask(client, alice)
    client.post(f"/api/messages/{a['message_id']}/feedback", json={"rating": "down", "reason": "meh"}, headers=alice)
    client.post("/api/escalations", json={"message_id": a["message_id"]}, headers=alice)

    r = client.get("/api/auth/me/export", headers=alice)
    assert "attachment" in r.headers["content-disposition"]
    data = json.loads(r.text)
    assert data["account"]["email"] == "alice@example.com"
    convo = data["conversations"][0]
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"] and convo["messages"][1]["feedback"] == -1
    assert data["questions_sent_to_staff"][0]["question"] == Q["question"]
    assert data["documents_you_uploaded"][0]["source"] == "refund_policy.md"
    assert "password" not in r.text.lower()
    assert client.get("/api/auth/me/export").status_code == 401


def test_deleting_your_account_erases_it(client, auth_on, refund_doc_for, db):
    from sqlalchemy import text

    signup(client, "boss@example.com")  # someone else is the admin
    alice = signup(client, "alice@example.com")
    refund_doc_for(alice)
    ask(client, alice)
    assert erase(client, alice, {"password": "wrong-password"}).status_code == 403
    assert erase(client, alice, {"password": "correct-horse"}).status_code == 200

    for table in ("users where email = 'alice@example.com'", "chat_sessions", "documents", "chunks", "chat_messages"):
        assert db.scalar(text(f"select count(*) from {table}")) == 0, table
    assert db.scalar(text("select count(*) from audit_logs")) == 0  # her questions are gone from the ledger too
    assert client.get("/api/auth/me", headers=alice).status_code == 401  # and her token no longer works


def test_the_audit_rows_can_be_kept_if_the_operator_prefers(client, auth_on, refund_doc_for, db, settings, monkeypatch):
    from sqlalchemy import text

    monkeypatch.setattr(settings, "keep_audit_on_account_delete", True)
    signup(client, "boss@example.com")
    alice = signup(client, "alice@example.com")
    refund_doc_for(alice)
    ask(client, alice)
    erase(client, alice, {"password": "correct-horse"})
    assert db.scalar(text("select count(*) from audit_logs")) == 1
    assert db.scalar(text("select count(*) from audit_logs where user_id is not null")) == 0  # but no longer linked to her


def test_the_only_admin_cannot_delete_their_account(client, auth_on):
    boss = signup(client, "boss@example.com")
    r = erase(client, boss, {"password": "correct-horse"})
    assert r.status_code == 409
    assert client.get("/api/auth/me", headers=boss).status_code == 200


def test_team_documents_outlive_the_member_who_deletes_their_account(client, auth_on, db):
    from sqlalchemy import text

    boss = signup(client, "boss@example.com")
    alice = signup(client, "alice@example.com")
    tid = client.post("/api/admin/teams", headers=boss, json={"name": "HR"}).json()["id"]
    uid = next(u["id"] for u in client.get("/api/admin/users", headers=boss).json() if u["email"] == "alice@example.com")
    client.put(f"/api/admin/teams/{tid}/members/{uid}", headers=boss)
    body = b"# Handbook\n\nEmployees receive twenty five vacation days.\n"
    client.post("/api/documents", headers=alice, data={"scope": "team", "team_id": str(tid)}, files={"file": ("handbook.md", body, "text/markdown")})
    erase(client, alice, {"password": "correct-horse"})
    assert db.scalar(text("select count(*) from documents where team_id is not null and owner_id is null")) == 1
