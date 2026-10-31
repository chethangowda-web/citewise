import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

GOOD = {
    "AUTH_ENABLED": "true", "JWT_SECRET": "j" * 48, "POSTGRES_PASSWORD": "p" * 24, "ADMIN_EMAIL": "owner@example.com",
    "ALLOW_SIGNUP": "false", "COOKIE_SECURE": "true", "PUBLIC_URL": "https://citewise.example.com",
    "GEMINI_API_KEY": "g" * 30, "LLM_PROVIDER": "groq", "GROQ_API_KEY": "q" * 30, "ML_GUARD": "groq",
    "SMTP_HOST": "smtp.example.com", "GLOBAL_DAILY_TOKEN_LIMIT": "400000", "RATE_LIMIT_PER_MINUTE": "10",
    "AUTH_RATE_LIMIT_PER_MINUTE": "10",
}


def levels(env):
    return [(lv, msg) for lv, msg in preflight.check(env)]


def fails(env):
    return [msg for lv, msg in levels(env) if lv == preflight.FAIL]


def warns(env):
    return [msg for lv, msg in levels(env) if lv == preflight.WARN]


def test_a_sound_production_setup_has_nothing_to_fix_and_nothing_to_warn_about():
    assert fails(GOOD) == [] and warns(GOOD) == []


@pytest.mark.parametrize(
    "change,expect",
    [
        ({"AUTH_ENABLED": "false"}, "AUTH_ENABLED"),
        ({"JWT_SECRET": ""}, "JWT_SECRET"),
        ({"JWT_SECRET": "short"}, "JWT_SECRET"),
        ({"POSTGRES_PASSWORD": "rag"}, "POSTGRES_PASSWORD"),
        ({"POSTGRES_PASSWORD": ""}, "POSTGRES_PASSWORD"),
        ({"ADMIN_API_KEY": "tooshort"}, "ADMIN_API_KEY"),
        ({"ALLOW_SIGNUP": "true", "ADMIN_EMAIL": ""}, "ADMIN_EMAIL"),
        ({"COOKIE_SECURE": "false"}, "COOKIE_SECURE"),
        ({"COOKIE_SECURE": "auto"}, "COOKIE_SECURE"),
        ({"PUBLIC_URL": "http://localhost:8080"}, "PUBLIC_URL"),
        ({"PUBLIC_URL": "http://citewise.example.com"}, "PUBLIC_URL"),
        ({"PUBLIC_URL": ""}, "PUBLIC_URL"),
        ({"GEMINI_API_KEY": ""}, "GEMINI_API_KEY"),
        ({"GROQ_API_KEY": ""}, "GROQ_API_KEY"),
        ({"GUARDRAILS_ENABLED": "false"}, "GUARDRAILS_ENABLED"),
        ({"AUTH_RATE_LIMIT_PER_MINUTE": "0"}, "AUTH_RATE_LIMIT_PER_MINUTE"),
        ({"AUTH_RATE_LIMIT_PER_MINUTE": "500"}, "AUTH_RATE_LIMIT_PER_MINUTE"),
    ],
)
def test_each_unsafe_choice_is_a_failure_that_names_the_setting(change, expect):
    msgs = fails({**GOOD, **change})
    assert any(expect in m for m in msgs), (change, msgs)


@pytest.mark.parametrize(
    "change,expect",
    [
        ({"ALLOW_SIGNUP": "true"}, "sign-up is open"),
        ({"SMTP_HOST": ""}, "SMTP_HOST"),
        ({"GLOBAL_DAILY_TOKEN_LIMIT": "0"}, "token limit"),
        ({"ML_GUARD": "off"}, "ML_GUARD"),
        ({"RATE_LIMIT_PER_MINUTE": "0"}, "RATE_LIMIT_PER_MINUTE"),
        ({"ALLOW_SIGNUP": "false", "ADMIN_EMAIL": ""}, "ADMIN_EMAIL"),
    ],
)
def test_risky_but_allowed_choices_are_warnings_not_failures(change, expect):
    env = {**GOOD, **change}
    assert fails(env) == [], fails(env)
    assert any(expect in m for m in warns(env)), (change, warns(env))


def test_the_production_template_names_every_setting_correctly():
    """Fill in the template's blanks the way a person would; if a name in it were misspelled, a check would fail."""
    env = preflight.parse_env((ROOT / ".env.production.example").read_text(encoding="utf-8"))
    filled = {
        **env, "GEMINI_API_KEY": "g" * 30, "GROQ_API_KEY": "q" * 30, "JWT_SECRET": "j" * 48, "ADMIN_EMAIL": "me@example.com",
        "POSTGRES_PASSWORD": "p" * 24, "ADMIN_API_KEY": "a" * 32, "PUBLIC_URL": "https://citewise.example.com",
        "SMTP_HOST": "smtp.example.com",
    }
    assert fails(filled) == [], fails(filled)


def test_the_production_template_ships_with_no_secret_filled_in():
    env = preflight.parse_env((ROOT / ".env.production.example").read_text(encoding="utf-8"))
    for key, value in env.items():
        if preflight.SECRET_NAME.search(key):
            assert value == "", f"{key} must be blank in the template"


def test_parsing_handles_comments_quotes_and_blank_lines():
    env = preflight.parse_env("# a comment\n\nA=1   # trailing\nB='two words'\nC=\"quoted\"\nD=\nnot a line\nE=x=y\n")
    assert env == {"A": "1", "B": "two words", "C": "quoted", "D": "", "E": "x=y"}


def run(env_text, tmp_path):
    f = tmp_path / "prod.env"
    f.write_text(env_text, encoding="utf-8")
    return subprocess.run(["python", str(ROOT / "scripts" / "preflight.py"), str(f)], capture_output=True, text=True)


def test_the_command_exits_non_zero_on_a_failure_and_never_prints_a_secret(tmp_path):
    bad = "\n".join(f"{k}={v}" for k, v in {**GOOD, "COOKIE_SECURE": "false"}.items())
    r = run(bad, tmp_path)
    assert r.returncode == 1 and "FAIL" in r.stdout and "COOKIE_SECURE" in r.stdout
    for secret in (GOOD["JWT_SECRET"], GOOD["GEMINI_API_KEY"], GOOD["POSTGRES_PASSWORD"], GOOD["GROQ_API_KEY"]):
        assert secret not in r.stdout + r.stderr


def test_the_command_exits_zero_for_a_sound_setup(tmp_path):
    r = run("\n".join(f"{k}={v}" for k, v in GOOD.items()), tmp_path)
    assert r.returncode == 0 and "0 problem(s)" in r.stdout, r.stdout


def test_a_missing_file_is_reported(tmp_path):
    r = subprocess.run(["python", str(ROOT / "scripts" / "preflight.py"), str(tmp_path / "nope.env")], capture_output=True, text=True)
    assert r.returncode == 1 and "does not exist" in r.stdout


# ---------- what would leak through git ----------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def make_repo(tmp_path, files):
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A", "-f"], cwd=tmp_path, check=True)
    return tmp_path


@needs_git
def test_a_tracked_env_file_or_backup_is_flagged(tmp_path):
    repo = make_repo(tmp_path, {".env": "JWT_SECRET=abc", "backups/x.dump": "d", "README.md": "hi"})
    msgs = [m for lv, m in preflight.repo_issues(repo) if lv == preflight.FAIL]
    assert any(".env is tracked" in m for m in msgs) and any("x.dump" in m for m in msgs)


@needs_git
def test_a_secret_in_the_committed_example_is_flagged_but_placeholders_are_not(tmp_path):
    leaky = make_repo(tmp_path / "leaky", {".env.example": "GEMINI_API_KEY=AIzaSyRealLookingKey1234567890\nJWT_SECRET=\nPOSTGRES_PASSWORD=rag\nLLM_PROVIDER=gemini  # gemini | groq\n"})
    msgs = [m for lv, m in preflight.repo_issues(leaky) if lv == preflight.FAIL]
    assert len(msgs) == 1 and "GEMINI_API_KEY" in msgs[0]
    assert "AIzaSy" not in msgs[0]  # it says which setting, never the value
    tidy = make_repo(tmp_path / "tidy", {".env.example": "GEMINI_API_KEY=\nJWT_SECRET=change-me\nPOSTGRES_PASSWORD=rag\n"})
    assert [m for lv, m in preflight.repo_issues(tidy) if lv == preflight.FAIL] == []


@needs_git
def test_this_repository_is_clean_right_now():
    """The guard against the mistake that nearly happened: a real secret pasted into the committed example."""
    assert [m for lv, m in preflight.repo_issues(ROOT) if lv == preflight.FAIL] == []
