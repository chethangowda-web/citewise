import re
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.models import LoginFailure, PasswordReset
from tests.test_auth import auth_on, signup  # noqa: F401  (auth_on is a fixture)

WEB = {"X-Requested-With": "citewise"}  # what the web app sends; it asks for a cookie session
PW = "correct-horse"


def login(client, email="alice@example.com", password=PW, **kw):
    return client.post("/api/auth/login", json={"email": email, "password": password}, **kw)


def token_of(client, email="alice@example.com", password=PW):
    return {"Authorization": "Bearer " + login(client, email, password).json()["token"]}


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.auth_routes.send_mail", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def reset_token(mail) -> str:
    return re.search(r"[?]reset=([\w-]+)", mail[2]).group(1)


# ---------- signing out everywhere ----------

def test_logout_everywhere_revokes_every_token(client, auth_on):
    a = signup(client, "alice@example.com")
    b = token_of(client)  # a second device
    assert client.get("/api/auth/me", headers=a).status_code == 200
    assert client.get("/api/auth/me", headers=b).status_code == 200
    assert client.post("/api/auth/logout-all", headers=a).status_code == 200
    assert client.get("/api/auth/me", headers=a).status_code == 401
    assert client.get("/api/auth/me", headers=b).status_code == 401
    assert client.get("/api/auth/me", headers=token_of(client)).status_code == 200  # signing in again works


def test_logout_everywhere_needs_a_session(client, auth_on):
    assert client.post("/api/auth/logout-all").status_code == 401


def test_tokens_issued_before_versioning_still_work_until_revoked(client, auth_on, settings):
    signup(client, "alice@example.com")
    uid = client.get("/api/auth/me", headers=token_of(client)).json()["id"]
    legacy = jwt.encode({"sub": str(uid), "exp": int(time.time()) + 600}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200


# ---------- changing the password ----------

def test_change_password(client, auth_on):
    old = signup(client, "alice@example.com")
    other_device = token_of(client)
    body = {"current_password": "wrong-password", "new_password": "brand-new-pass"}
    assert client.post("/api/auth/change-password", headers=old, json=body).status_code == 403
    weak = {"current_password": PW, "new_password": "short"}
    assert client.post("/api/auth/change-password", headers=old, json=weak).status_code == 422

    ok = client.post("/api/auth/change-password", headers=old, json={"current_password": PW, "new_password": "brand-new-pass"})
    assert ok.status_code == 200
    fresh = {"Authorization": "Bearer " + ok.json()["token"]}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200  # this device stays signed in
    assert client.get("/api/auth/me", headers=old).status_code == 401  # every older session is gone
    assert client.get("/api/auth/me", headers=other_device).status_code == 401
    assert login(client).status_code == 401  # the old password is dead
    assert login(client, password="brand-new-pass").status_code == 200


# ---------- lockout ----------

def test_repeated_wrong_passwords_lock_the_account_even_for_the_right_one(client, auth_on):
    signup(client, "alice@example.com")
    codes = [login(client, password="nope-nope-nope").status_code for _ in range(5)]
    assert codes == [401] * 5
    locked = login(client)  # now the correct password is refused too
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    assert login(client, "someone-else@example.com").status_code == 401  # other addresses are unaffected


def test_a_lockout_looks_identical_for_accounts_that_do_not_exist(client, auth_on):
    signup(client, "alice@example.com")
    real = [login(client, "alice@example.com", "nope-nope-nope") for _ in range(6)]
    ghost = [login(client, "ghost@example.com", "nope-nope-nope") for _ in range(6)]
    assert [r.status_code for r in real] == [r.status_code for r in ghost] == [401] * 5 + [429]
    assert real[-1].json() == ghost[-1].json()  # same message, so lockout cannot be used to find accounts
    assert ("Retry-After" in real[-1].headers) == ("Retry-After" in ghost[-1].headers)


def test_a_lock_expires_and_a_good_login_clears_the_count(client, auth_on, db):
    signup(client, "alice@example.com")
    for _ in range(5):
        login(client, password="nope-nope-nope")
    assert login(client).status_code == 429
    row = db.get(LoginFailure, "alice@example.com")
    row.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    assert login(client).status_code == 200
    db.expire_all()
    assert db.get(LoginFailure, "alice@example.com") is None


def test_a_success_forgives_earlier_failures(client, auth_on):
    signup(client, "alice@example.com")
    for _ in range(4):
        login(client, password="nope-nope-nope")
    assert login(client).status_code == 200  # fourth strike was not the fifth
    for _ in range(4):
        assert login(client, password="nope-nope-nope").status_code == 401  # the count started over
    assert login(client).status_code == 200


def test_lockout_can_be_turned_off(client, auth_on, settings, monkeypatch):
    monkeypatch.setattr(settings, "lockout_attempts", 0)
    signup(client, "alice@example.com")
    assert [login(client, password="nope-nope-nope").status_code for _ in range(8)] == [401] * 8
    assert login(client).status_code == 200


# ---------- password reset ----------

def test_reset_flow_end_to_end(client, auth_on, mails, db):
    old = signup(client, "alice@example.com")
    assert client.post("/api/auth/forgot", json={"email": "Alice@Example.com"}).json()["ok"] is True
    assert len(mails) == 1 and mails[0][0] == "alice@example.com"
    token = reset_token(mails[0])

    row = db.query(PasswordReset).one()
    assert row.token_hash != token and token not in str(row.__dict__)  # only a hash is stored

    weak = client.post("/api/auth/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 400  # rejected, and the link is not used up
    assert client.post("/api/auth/reset", json={"token": token, "password": "a-new-password"}).status_code == 200

    assert client.post("/api/auth/reset", json={"token": token, "password": "another-pass1"}).status_code == 400  # single use
    assert login(client, password=PW).status_code == 401
    assert login(client, password="a-new-password").status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401  # a reset signs everyone out


def test_asking_for_a_reset_reveals_nothing_about_who_has_an_account(client, auth_on, mails):
    signup(client, "alice@example.com")
    known = client.post("/api/auth/forgot", json={"email": "alice@example.com"})
    unknown = client.post("/api/auth/forgot", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert [m[0] for m in mails] == ["alice@example.com"]  # no mail for the ghost
    assert "token" not in known.text and "reset=" not in known.text  # the link only ever goes by email


def test_reset_emails_are_limited_per_address(client, auth_on, mails):
    signup(client, "alice@example.com")
    replies = [client.post("/api/auth/forgot", json={"email": "alice@example.com"}).json() for _ in range(5)]
    assert len(mails) == 3  # mail-bombing an inbox is capped, silently
    assert all(r == replies[0] for r in replies)


def test_a_newer_link_cancels_the_older_one(client, auth_on, mails):
    signup(client, "alice@example.com")
    for _ in range(2):
        client.post("/api/auth/forgot", json={"email": "alice@example.com"})
    first, second = (reset_token(m) for m in mails)
    assert client.post("/api/auth/reset", json={"token": first, "password": "a-new-password"}).status_code == 400
    assert client.post("/api/auth/reset", json={"token": second, "password": "a-new-password"}).status_code == 200


def test_an_expired_link_is_refused(client, auth_on, mails, db):
    signup(client, "alice@example.com")
    client.post("/api/auth/forgot", json={"email": "alice@example.com"})
    row = db.query(PasswordReset).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert client.post("/api/auth/reset", json={"token": reset_token(mails[0]), "password": "a-new-password"}).status_code == 400


def test_a_made_up_token_is_refused(client, auth_on):
    assert client.post("/api/auth/reset", json={"token": "x" * 40, "password": "a-new-password"}).status_code == 400


def test_a_reset_lifts_a_lockout(client, auth_on, mails):
    signup(client, "alice@example.com")
    for _ in range(5):
        login(client, password="nope-nope-nope")
    assert login(client).status_code == 429
    client.post("/api/auth/forgot", json={"email": "alice@example.com"})
    client.post("/api/auth/reset", json={"token": reset_token(mails[0]), "password": "a-new-password"})
    assert login(client, password="a-new-password").status_code == 200


def test_reset_endpoints_do_not_exist_without_accounts(client):
    assert client.post("/api/auth/forgot", json={"email": "a@b.co"}).status_code == 404
    assert client.post("/api/auth/reset", json={"token": "x", "password": "longenough"}).status_code == 404


# ---------- cookie sessions and CSRF ----------

def test_the_web_app_gets_an_http_only_cookie_and_never_sees_the_token(client, auth_on):
    r = client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW}, headers=WEB)
    assert r.status_code == 200 and "token" not in r.json()
    cookie = r.headers["set-cookie"].lower()
    assert "citewise_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert client.get("/api/auth/me").json()["email"] == "alice@example.com"  # the cookie alone authenticates


def test_other_clients_still_get_a_token_and_no_cookie(client, auth_on):
    r = client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW})
    assert r.json()["token"] and "set-cookie" not in r.headers
    assert client.get("/api/auth/me").status_code == 401  # nothing was stored


def test_cookie_requests_that_change_data_need_the_csrf_header(client, auth_on):
    client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW}, headers=WEB)
    q = {"question": "hi"}
    assert client.get("/api/documents").status_code == 200  # reading needs no header
    assert client.post("/api/chat", json=q).status_code == 403  # a cross-site form post would look like this
    assert client.post("/api/chat", json=q, headers={"X-Requested-With": "something-else"}).status_code == 403
    assert client.delete("/api/sessions/none").status_code == 403
    assert client.post("/api/chat", json=q, headers=WEB).status_code == 200


def test_bearer_requests_need_no_csrf_header(client, auth_on):
    h = signup(client, "alice@example.com")
    assert client.post("/api/chat", json={"question": "hi"}, headers=h).status_code == 200


def test_a_bearer_token_takes_precedence_over_the_cookie(client, auth_on):
    client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW}, headers=WEB)
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_logging_out_clears_the_cookie(client, auth_on):
    client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW}, headers=WEB)
    r = client.post("/api/auth/logout")
    assert "citewise_session=" in r.headers["set-cookie"].lower() and "max-age=0" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").status_code == 401


def test_a_forged_cookie_is_rejected(client, auth_on):
    client.cookies.set("citewise_session", "not-a-real-token")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_everywhere_also_kills_cookie_sessions(client, auth_on):
    client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW}, headers=WEB)
    assert client.post("/api/auth/logout-all", headers=WEB).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize(
    "setting,proxy,forwarded,secure",
    [("auto", False, None, False), ("true", False, None, True), ("false", True, "https", False), ("auto", True, "https", True)],
)
def test_cookie_secure_flag(client, auth_on, settings, monkeypatch, setting, proxy, forwarded, secure):
    monkeypatch.setattr(settings, "cookie_secure", setting)
    monkeypatch.setattr(settings, "trusted_proxy", proxy)
    headers = dict(WEB, **({"X-Forwarded-Proto": forwarded} if forwarded else {}))
    r = client.post("/api/auth/register", json={"email": "alice@example.com", "password": PW}, headers=headers)
    assert ("secure" in r.headers["set-cookie"].lower().split("; ")) is secure
