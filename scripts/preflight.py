#!/usr/bin/env python3
"""Check a production configuration before it goes anywhere public.

    python scripts/preflight.py                      # checks ./.env
    python scripts/preflight.py path/to/.env.prod

Prints one line per finding and exits 1 if anything is a FAIL. WARN means "you can go ahead, knowingly".
Secrets are never printed. Needs only Python 3.9+, no packages.
"""
import re
import subprocess
import sys
from pathlib import Path

FAIL, WARN, OK = "FAIL", "WARN", "ok  "
SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD)$")
# values that are obviously placeholders, not secrets
PLACEHOLDER = re.compile(r"^(|change-me.*|your[-_ ].*|<.*>|xxx+|rag)$", re.IGNORECASE)


def parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"\s+#.*$", "", value).strip()  # trailing comment
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def check(env: dict[str, str]) -> list[tuple[str, str]]:
    """Every finding for this configuration, as (level, message)."""
    out: list[tuple[str, str]] = []

    def add(level: str, msg: str) -> None:
        out.append((level, msg))

    def need(cond: bool, ok: str, bad: str, level: str = FAIL) -> None:
        add(OK if cond else level, ok if cond else bad)

    get = lambda k, d="": env.get(k, d)  # noqa: E731

    # --- who can get in
    need(truthy(get("AUTH_ENABLED")), "accounts are on", "AUTH_ENABLED must be true: otherwise anyone can upload and delete documents")
    secret = get("JWT_SECRET")
    need(len(secret) >= 32, "JWT_SECRET is long enough", "JWT_SECRET must be a random value of at least 32 characters (openssl rand -hex 32)")
    need(
        get("POSTGRES_PASSWORD", "rag") not in ("", "rag"),
        "the database password is not the default",
        "POSTGRES_PASSWORD is the default 'rag': set a long random one before the first start",
    )
    admin_key = get("ADMIN_API_KEY")
    if admin_key:
        need(len(admin_key) >= 20, "ADMIN_API_KEY is long enough", "ADMIN_API_KEY is set but short: use at least 20 random characters")
    else:
        add(OK, "no ADMIN_API_KEY: the admin API works only for admin accounts (fine)")
    signup_open = truthy(get("ALLOW_SIGNUP", "true"))
    if get("ADMIN_EMAIL"):
        add(OK, "ADMIN_EMAIL names the administrator")
    elif signup_open:
        add(FAIL, "ADMIN_EMAIL is empty while sign-up is open: whoever registers first becomes admin. Set ADMIN_EMAIL to your address")
    else:
        add(WARN, "ADMIN_EMAIL is empty: the first account to register becomes admin")
    if signup_open:
        add(WARN, "sign-up is open to anyone; set ALLOW_SIGNUP=false once your people have accounts")
    else:
        add(OK, "sign-up is closed")

    # --- the connection
    need(truthy(get("COOKIE_SECURE")) , "the session cookie is Secure", "COOKIE_SECURE must be true once you serve over HTTPS, or the session cookie can travel in the clear")
    url = get("PUBLIC_URL")
    need(
        url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url,
        "PUBLIC_URL is an https address",
        "PUBLIC_URL must be your real https address (it is used in password-reset links)",
    )

    # --- the model
    need(bool(get("GEMINI_API_KEY")), "GEMINI_API_KEY is set (embeddings need it)", "GEMINI_API_KEY is empty: documents cannot be indexed or searched")
    if get("LLM_PROVIDER", "gemini") == "groq":
        need(bool(get("GROQ_API_KEY")), "GROQ_API_KEY is set", "LLM_PROVIDER=groq but GROQ_API_KEY is empty")
    need(truthy(get("GUARDRAILS_ENABLED", "true")), "guardrails are on", "GUARDRAILS_ENABLED must not be false in production (it exists for evaluation only)")
    if get("ML_GUARD", "off") == "off":
        add(WARN, "ML_GUARD is off: only the pattern filter screens for prompt injection (ML_GUARD=groq catches far more)")
    else:
        add(OK, "the ML injection guard is on")

    # --- abuse and cost
    auth_limit = get("AUTH_RATE_LIMIT_PER_MINUTE", "10")
    need(auth_limit.isdigit() and 0 < int(auth_limit) <= 30, "sign-in attempts are rate limited", "AUTH_RATE_LIMIT_PER_MINUTE must be between 1 and 30 on a public server")
    rate = get("RATE_LIMIT_PER_MINUTE", "20")
    need(rate.isdigit() and 0 < int(rate) <= 60, "questions are rate limited", "RATE_LIMIT_PER_MINUTE should be between 1 and 60 on a public server (0 turns it off)", WARN)
    limits = [get("USER_DAILY_TOKEN_LIMIT", "0"), get("GLOBAL_DAILY_TOKEN_LIMIT", "0")]
    if all(v in ("", "0") for v in limits):
        add(WARN, "no daily token limit is set: a busy day or a runaway script is limited only by your provider's quota. Set GLOBAL_DAILY_TOKEN_LIMIT")
    else:
        add(OK, "a daily token limit is set")

    # --- mail
    if get("SMTP_HOST"):
        add(OK, "SMTP is configured: reset emails will be sent")
    else:
        add(WARN, "SMTP_HOST is empty: password-reset links are written to the log (a credential), and nobody receives them")

    # --- proxies
    add(OK if get("TRUSTED_PROXY", "true") != "false" else WARN, "client addresses are read from the proxy header (the bundled nginx sets it)")
    return out


def repo_issues(root: Path) -> list[tuple[str, str]]:
    """Mistakes that would leak a secret through git."""
    out: list[tuple[str, str]] = []

    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    tracked = set(git("ls-files").split())
    if not tracked:
        return [(WARN, "not a git checkout: could not check what would be committed")]
    for f in sorted(tracked):
        if f == ".env" or f.endswith(".dump") or f.startswith("backups/"):
            out.append((FAIL, f"{f} is tracked by git: remove it (git rm --cached) and keep it out of the repository"))
    example = root / ".env.example"
    if ".env.example" in tracked and example.exists():
        for key, value in parse_env(example.read_text(encoding="utf-8")).items():
            if SECRET_NAME.search(key) and not PLACEHOLDER.match(value):
                out.append((FAIL, f".env.example has a value for {key}: it is committed to git, so keep it blank"))
    if not out:
        out.append((OK, "no secrets or backups are tracked by git"))
    return out


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    path = Path(argv[1]) if len(argv) > 1 else root / ".env"
    if not path.exists():
        print(f"FAIL  {path} does not exist")
        return 1
    findings = check(parse_env(path.read_text(encoding="utf-8"))) + repo_issues(root)
    for level, msg in findings:
        print(f"{level}  {msg}")
    fails = sum(1 for lv, _ in findings if lv == FAIL)
    warns = sum(1 for lv, _ in findings if lv == WARN)
    print(f"\n{fails} problem(s) to fix, {warns} warning(s) to consider.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
