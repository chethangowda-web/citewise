import pytest

from tests.test_auth import auth_on, signup, upload  # noqa: F401  (auth_on is a fixture)

HR_DOC = b"# Salary Bands\n\nThe engineering salary band tops out at two hundred thousand dollars.\n"
Q = {"question": "What is the engineering salary band?"}


def team(client, admin, name="HR"):
    r = client.post("/api/admin/teams", headers=admin, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def join(client, admin, team_id, who):
    uid = next(u["id"] for u in client.get("/api/admin/users", headers=admin).json() if u["email"] == who)
    assert client.put(f"/api/admin/teams/{team_id}/members/{uid}", headers=admin).status_code == 204
    return uid


def team_upload(client, headers, team_id, name="salary_bands.md"):
    return client.post(
        "/api/documents", headers=headers, data={"scope": "team", "team_id": str(team_id)},
        files={"file": (name, HR_DOC, "text/markdown")},
    )


@pytest.fixture
def org(client, auth_on):  # noqa: F811
    """boss (admin), alice (editor), bob (editor), vera (viewer-by-promotion)."""
    boss = signup(client, "boss@example.com")
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    return boss, alice, bob


def test_first_account_is_admin_and_later_ones_are_editors(client, org):
    boss, alice, _ = org
    assert client.get("/api/auth/me", headers=boss).json()["role"] == "admin"
    me = client.get("/api/auth/me", headers=alice).json()
    assert me["role"] == "editor" and me["teams"] == []


def test_default_role_can_be_viewer(client, org, settings, monkeypatch):
    monkeypatch.setattr(settings, "default_role", "viewer")
    viewer = signup(client, "vera@example.com")
    assert client.get("/api/auth/me", headers=viewer).json()["role"] == "viewer"
    assert upload(client, viewer).status_code == 403  # viewers can read and ask, not upload
    assert client.get("/api/documents", headers=viewer).status_code == 200
    assert client.post("/api/chat", headers=viewer, json=Q).status_code == 200


def test_only_admins_reach_the_admin_api(client, org, settings, monkeypatch):
    boss, alice, _ = org
    for path in ("/api/admin/users", "/api/admin/teams", "/api/admin/documents", "/api/admin/stats", "/api/admin/logs"):
        assert client.get(path, headers=alice).status_code == 403, path
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=boss).status_code == 200, path
    # the admin key (for scripts) still works without an account
    assert client.get("/api/admin/users", headers={"X-Admin-Key": "test-admin"}).status_code == 200
    assert client.get("/api/admin/users", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_role_changes_and_the_last_admin_is_protected(client, org):
    boss, alice, _ = org
    users = {u["email"]: u["id"] for u in client.get("/api/admin/users", headers=boss).json()}
    assert client.patch(f"/api/admin/users/{users['boss@example.com']}", headers=boss, json={"role": "editor"}).status_code == 409
    assert client.delete(f"/api/admin/users/{users['boss@example.com']}", headers=boss).status_code == 409
    assert client.patch(f"/api/admin/users/{users['alice@example.com']}", headers=boss, json={"role": "superuser"}).status_code == 422
    r = client.patch(f"/api/admin/users/{users['alice@example.com']}", headers=boss, json={"role": "admin"})
    assert r.json()["role"] == "admin"
    assert client.get("/api/admin/users", headers=alice).status_code == 200  # takes effect on the next request
    # with two admins, one may step down
    assert client.patch(f"/api/admin/users/{users['boss@example.com']}", headers=alice, json={"role": "viewer"}).status_code == 200


def test_team_documents_are_visible_to_members_only(client, org, llm):
    boss, alice, bob = org
    hr = team(client, boss)
    join(client, boss, hr, "alice@example.com")
    assert team_upload(client, alice, hr).json()["scope"] == "team"

    listed = client.get("/api/documents", headers=alice).json()
    assert listed[0]["scope"] == "team" and listed[0]["team"] == "HR"
    assert client.get("/api/documents", headers=bob).json() == []
    assert client.post("/api/chat", headers=bob, json=Q).json()["outcome"] == "refused" and not llm.calls

    carol_uid = join(client, boss, hr, "bob@example.com")  # bob joins and now sees and can search it
    assert [d["title"] for d in client.get("/api/documents", headers=bob).json()] == ["Salary Bands"]
    assert client.post("/api/chat", headers=bob, json=Q).json()["outcome"] == "answered"

    # removing him revokes access immediately
    assert client.delete(f"/api/admin/teams/{hr}/members/{carol_uid}", headers=boss).status_code == 204
    assert client.get("/api/documents", headers=bob).json() == []
    assert client.post("/api/chat", headers=bob, json=Q).json()["outcome"] == "refused"


def test_upload_scope_rules(client, org):
    boss, alice, _ = org
    hr = team(client, boss)
    assert team_upload(client, alice, hr).status_code == 403  # alice is not in HR
    assert team_upload(client, alice, 9999).status_code == 403
    assert team_upload(client, boss, hr).status_code == 200  # admins may upload into any team
    shared = {"scope": "shared"}
    body = {"file": ("handbook.md", HR_DOC, "text/markdown")}
    assert client.post("/api/documents", headers=alice, data=shared, files=body).status_code == 403
    assert client.post("/api/documents", headers=boss, data=shared, files=body).json()["scope"] == "shared"
    assert client.post("/api/documents", headers=alice, data={"scope": "bogus"}, files=body).status_code == 422
    assert [d["scope"] for d in client.get("/api/documents", headers=alice).json()] == ["shared"]  # everyone sees shared


def test_who_may_delete_a_team_document(client, org):
    boss, alice, bob = org
    hr = team(client, boss)
    join(client, boss, hr, "alice@example.com")
    join(client, boss, hr, "bob@example.com")
    doc = team_upload(client, alice, hr).json()["id"]
    assert client.delete(f"/api/documents/{doc}", headers=bob).status_code == 403  # a teammate, not the uploader
    outsider = signup(client, "dave@example.com")
    assert client.delete(f"/api/documents/{doc}", headers=outsider).status_code == 404  # cannot even see it
    assert client.delete(f"/api/documents/{doc}", headers=alice).status_code == 204
    doc2 = team_upload(client, alice, hr).json()["id"]
    assert client.delete(f"/api/documents/{doc2}", headers=boss).status_code == 204  # admin


def test_deleting_a_user_keeps_team_documents_private_to_the_team(client, org):
    boss, alice, bob = org
    hr = team(client, boss)
    uid = join(client, boss, hr, "alice@example.com")
    team_upload(client, alice, hr)
    upload(client, alice, "mine.md")  # alice's private file
    assert client.delete(f"/api/admin/users/{uid}", headers=boss).status_code == 204

    docs = client.get("/api/admin/documents", headers=boss).json()
    assert [(d["title"], d["scope"], d["owner"], d["team"]) for d in docs] == [("Salary Bands", "team", None, "HR")]
    assert client.get("/api/documents", headers=bob).json() == []  # orphaned team docs must NOT become shared


def test_team_management(client, org):
    boss, alice, _ = org
    hr = team(client, boss)
    assert client.post("/api/admin/teams", headers=boss, json={"name": "HR"}).status_code == 409
    assert client.post("/api/admin/teams", headers=boss, json={"name": ""}).status_code == 422
    assert client.post("/api/admin/teams", headers=alice, json={"name": "Mine"}).status_code == 403
    join(client, boss, hr, "alice@example.com")
    join(client, boss, hr, "alice@example.com")  # adding twice is harmless
    listed = client.get("/api/admin/teams", headers=boss).json()
    assert listed[0]["members"] == [{"id": listed[0]["members"][0]["id"], "email": "alice@example.com"}]
    assert client.get("/api/auth/me", headers=alice).json()["teams"] == [{"id": hr, "name": "HR"}]

    team_upload(client, alice, hr)
    assert client.delete(f"/api/admin/teams/{hr}", headers=boss).status_code == 409  # still has documents
    doc = client.get("/api/documents", headers=alice).json()[0]["id"]
    client.delete(f"/api/documents/{doc}", headers=alice)
    assert client.delete(f"/api/admin/teams/{hr}", headers=boss).status_code == 204
    assert client.delete(f"/api/admin/teams/{hr}", headers=boss).status_code == 404


def test_admin_sees_every_document_but_chat_only_searches_what_they_can_see(client, org, llm):
    boss, alice, _ = org
    upload(client, alice, "salary_bands.md", HR_DOC)  # alice's private file
    rows = client.get("/api/admin/documents", headers=boss).json()
    assert [(r["scope"], r["owner"]) for r in rows] == [("private", "alice@example.com")]
    assert client.get("/api/documents", headers=boss).json() == []  # not in the admin's own library
    assert client.post("/api/chat", headers=boss, json=Q).json()["outcome"] == "refused"  # and not in their answers


def test_auth_disabled_keeps_the_old_open_behaviour(client):
    assert client.post("/api/documents", files={"file": ("a.md", HR_DOC, "text/markdown")}).json()["scope"] == "shared"
    assert client.get("/api/admin/users").status_code == 401 or client.get("/api/admin/users").status_code == 404


def test_startup_promotes_the_oldest_account_when_nobody_is_admin(client, db):
    """Databases that predate roles have users but no admin; the oldest user must not be locked out."""
    from sqlalchemy import text

    from app.db import init_db
    from app.models import User

    db.add_all([User(email="old@example.com", password_hash="x", role="editor"), User(email="new@example.com", password_hash="x", role="editor")])
    db.commit()
    init_db()
    db.expire_all()
    roles = {u.email: u.role for u in db.query(User)}
    assert roles == {"old@example.com": "admin", "new@example.com": "editor"}
    db.rollback()  # release this session's read lock, or the next ALTER TABLE would wait on it forever
    init_db()  # idempotent: running it again changes nothing
    assert db.scalar(text("select count(*) from users where role = 'admin'")) == 1


def test_admin_key_alone_can_delete_a_document_when_auth_is_on(client, org):
    boss, alice, _ = org
    doc = upload(client, alice).json()["id"]
    assert client.delete(f"/api/documents/{doc}").status_code == 401  # no token, no key
    assert client.delete(f"/api/documents/{doc}", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.delete(f"/api/documents/{doc}", headers={"X-Admin-Key": "test-admin"}).status_code == 204
