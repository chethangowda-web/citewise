from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    # Lightweight forward-only migration for databases created before these columns existed.
    with engine.begin() as conn:
        for ddl in (
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
            # source used to be globally unique; it is now unique per owner (see models.py)
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
            # the unique key now includes the team (see models.py)
            "DROP INDEX IF EXISTS uq_documents_owner_source",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(12) NOT NULL DEFAULT 'current'",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS effective_date DATE",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)",
            # only current documents must be unique per source: old versions are kept beside them
            "DROP INDEX IF EXISTS uq_documents_scope_source",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_scope_source "
            "ON documents ((coalesce(owner_id, 0)), (coalesce(team_id, 0)), source) WHERE status = 'current'",
        ):
            conn.execute(text(ddl))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
