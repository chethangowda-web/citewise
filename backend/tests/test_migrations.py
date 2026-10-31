import threading
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.db import BASELINE, Base, alembic_config, run_migrations
from tests.conftest import TEST_DB_URL


@pytest.fixture
def scratch():
    """A throwaway empty database, so migrations can be tried from scratch without touching the test database."""
    base = make_url(TEST_DB_URL)
    name = f"rag_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(base.set(database=name))
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def drift(engine):
    """What the models say that the database does not (and the reverse). Empty means they agree."""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_object": lambda o, n, t, r, c: not (t == "table" and n == "alembic_version")}
        )
        return compare_metadata(ctx, Base.metadata)


def version(engine):
    with engine.connect() as conn:
        return conn.scalar(text("select version_num from alembic_version"))


def test_a_new_database_is_built_by_the_migrations_and_matches_the_models_exactly(scratch):
    run_migrations(scratch)
    assert version(scratch) == BASELINE
    tables = set(inspect(scratch).get_table_names())
    assert {"users", "documents", "chunks", "chat_sessions", "chat_messages", "audit_logs", "teams", "team_members",
            "password_resets", "login_failures", "escalations", "ingest_jobs"} <= tables
    # If this fails, a model changed without a migration: run `alembic revision --autogenerate`.
    assert drift(scratch) == []


def test_running_it_again_changes_nothing(scratch):
    run_migrations(scratch)
    with scratch.begin() as c:
        c.execute(text("insert into users (email, password_hash, created_at) values ('a@b.co', 'x', now())"))
    run_migrations(scratch)
    assert version(scratch) == BASELINE
    with scratch.connect() as c:
        assert c.scalar(text("select count(*) from users")) == 1


def test_a_database_from_before_migrations_is_adopted_with_its_data_intact(scratch):
    # Build the shape an older release had: the current tables, minus everything added since, with the old index.
    with scratch.begin() as c:
        c.execute(text("create extension if not exists vector"))
    Base.metadata.create_all(scratch)
    with scratch.begin() as c:
        for col in ("version", "status", "effective_date", "content_hash"):
            c.execute(text(f"alter table documents drop column {col}"))
        c.execute(text("drop index if exists uq_documents_scope_source"))  # dropping the status column already took it
        c.execute(text("create unique index uq_documents_owner_source on documents ((coalesce(owner_id, 0)), source)"))
        c.execute(text("alter table users drop column token_version"))
        c.execute(text("alter table users drop column role"))
        c.execute(text("insert into users (email, password_hash, created_at) values ('old@example.com', 'x', now()), ('newer@example.com', 'x', now())"))
        c.execute(text("insert into documents (title, source, created_at) values ('Refund Policy', 'refund.md', now())"))
        c.execute(text("insert into chunks (document_id, position, content, embedding) "
                       "select id, 0, 'refunds in 30 days', array_fill(0.1::real, ARRAY[768])::vector from documents"))
    assert "alembic_version" not in inspect(scratch).get_table_names()
    assert drift(scratch) != []  # it really is out of date

    run_migrations(scratch)

    assert version(scratch) == BASELINE
    assert drift(scratch) == []  # brought exactly up to the baseline
    with scratch.connect() as c:
        assert c.scalar(text("select count(*) from documents where status = 'current' and version = 1")) == 1
        assert c.scalar(text("select count(*) from chunks")) == 1  # nothing was lost
        roles = dict(c.execute(text("select email, role from users")).all())
    assert roles == {"old@example.com": "admin", "newer@example.com": "editor"}  # the oldest account can administer


def test_two_servers_starting_together_take_turns(scratch):
    errors, engines = [], [scratch, create_engine(scratch.url), create_engine(scratch.url)]

    def go(engine):
        try:
            run_migrations(engine)
        except Exception as e:  # pragma: no cover - only reached when the lock fails
            errors.append(e)

    threads = [threading.Thread(target=go, args=(e,)) for e in engines]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    for e in engines[1:]:
        e.dispose()
    assert errors == []
    assert version(scratch) == BASELINE and drift(scratch) == []
    with scratch.connect() as c:
        assert c.scalar(text("select count(*) from alembic_version")) == 1


def test_the_baseline_can_be_undone_and_redone(scratch):
    run_migrations(scratch)
    with scratch.connect() as conn:
        command.downgrade(alembic_config(conn), "base")
        conn.commit()
    assert set(inspect(scratch).get_table_names()) <= {"alembic_version"}
    run_migrations(scratch)
    assert drift(scratch) == []


def test_a_future_revision_would_be_applied_on_top_of_the_baseline(scratch, tmp_path, monkeypatch):
    """The upgrade path for the next schema change, tried with a throwaway revision."""
    import shutil
    from pathlib import Path

    migrations = Path(__file__).resolve().parents[1] / "migrations"
    copy = tmp_path / "migrations"
    shutil.copytree(migrations, copy)
    (copy / "versions" / "0002_demo.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\nrevision = '0002'\ndown_revision = '0001'\nbranch_labels = None\ndepends_on = None\n"
        "def upgrade():\n    op.add_column('teams', sa.Column('note', sa.String(20), nullable=True))\n"
        "def downgrade():\n    op.drop_column('teams', 'note')\n"
    )
    monkeypatch.setattr("app.db.Path", lambda *_a, **_k: type("P", (), {"resolve": lambda s: type("R", (), {"parents": {1: copy.parent}})()})())
    run_migrations(scratch)
    assert version(scratch) == "0002"
    assert "note" in {c["name"] for c in inspect(scratch).get_columns("teams")}
