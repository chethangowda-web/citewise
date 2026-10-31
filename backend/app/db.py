from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

BASELINE = "0001"  # the first migration: the whole schema as it was when migrations were adopted
MIGRATION_LOCK = 727274  # Postgres advisory-lock id: only one server migrates at a time


class Base(DeclarativeBase):
    pass


# How databases created before migrations existed were brought up to date, one idempotent step at a time.
# Kept only to adopt such databases (see run_migrations); every change from now on is an Alembic revision.
_LEGACY_STEPS = (
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_id VARCHAR(32) NOT NULL DEFAULT ''",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS outcome VARCHAR(16) NOT NULL DEFAULT 'answered'",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS model VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS completion_tokens INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_outcome ON audit_logs (outcome)",
    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS quarantined BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS ix_documents_owner_id ON documents (owner_id)",
    "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_key",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(10) NOT NULL DEFAULT 'editor'",
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS feedback INTEGER",
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS feedback_reason TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
    # Accounts that predate roles: someone must be able to administer, so the oldest one becomes admin.
    "UPDATE users SET role = 'admin' WHERE id = (SELECT min(id) FROM users) "
    "AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin')",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS ix_documents_team_id ON documents (team_id)",
    "DROP INDEX IF EXISTS uq_documents_owner_source",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(12) NOT NULL DEFAULT 'current'",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS effective_date DATE",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)",
    "DROP INDEX IF EXISTS uq_documents_scope_source",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_scope_source "
    "ON documents ((coalesce(owner_id, 0)), (coalesce(team_id, 0)), source) WHERE status = 'current'",
)


def alembic_config(connection: Connection | None = None) -> Config:
    """Alembic set up from code (no .ini needed inside the container). `connection` lets the caller keep the
    migration lock on the very connection the migrations run on."""
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.attributes["configure_logging"] = False
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def _adopt_legacy_database(conn: Connection) -> None:
    """A database from before migrations: bring it to the baseline's shape without touching its data."""
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(conn)  # tables added since it was created
    for ddl in _LEGACY_STEPS:
        conn.execute(text(ddl))
    conn.commit()


def run_migrations(target: Engine | None = None) -> None:
    """Bring the database to the newest schema.

    - empty database: build it from the migrations
    - database from before migrations existed: bring it up to the baseline, stamp it there, then migrate on
    - database already under migrations: apply whatever is new
    A Postgres advisory lock makes servers that start at the same moment take turns instead of colliding.
    """
    with (target or engine).connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK})
        conn.commit()
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
            cfg = alembic_config(conn)
            tables = set(inspect(conn).get_table_names())
            if "alembic_version" not in tables and "documents" in tables:
                _adopt_legacy_database(conn)
                command.stamp(cfg, BASELINE)
                conn.commit()
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK})
            conn.commit()


def init_db() -> None:
    run_migrations()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
