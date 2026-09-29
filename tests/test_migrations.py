import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from ibig_agent import migrate
from ibig_agent.db import Base, init_db

# En CI, les migrations sont aussi jouées sur PostgreSQL (base de production).
POSTGRES_URL = os.environ.get("IBIG_TEST_POSTGRES_URL")


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        yield create_engine(f"sqlite:///{tmp_path / 'm.db'}")
        return
    if not POSTGRES_URL:
        pytest.skip("IBIG_TEST_POSTGRES_URL non défini")
    eng = create_engine(POSTGRES_URL)
    _reset(eng)
    yield eng
    _reset(eng)
    eng.dispose()


def _reset(eng):
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")


def test_migrations_match_models(engine):
    """Toute modification de db.py doit s'accompagner d'une migration."""
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == migrate.head_revision()
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"Migration manquante (ibig-agent migrer --nouvelle ...) : {diff}"


def test_upgrade_is_idempotent_and_reversible(engine):
    migrate.upgrade(engine)
    migrate.upgrade(engine)
    from alembic import command

    with engine.begin() as conn:
        cfg = migrate._config()
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    assert not inspect(engine).has_table("journal")
    migrate.upgrade(engine)
    assert inspect(engine).has_table("users")


def test_legacy_database_is_refused_then_stamped(engine):
    init_db(engine)  # base créée sans migrations
    with pytest.raises(RuntimeError, match="sans migrations"):
        migrate.upgrade(engine)
    migrate.stamp(engine)
    migrate.upgrade(engine)
    assert migrate.current_revision(engine) == migrate.head_revision()


def test_new_not_null_column_applies_to_existing_rows(engine):
    """Une migration doit passer sur une base qui contient déjà des données."""
    from sqlalchemy import text

    migrate.upgrade(engine, "0001")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO processed_messages (mailbox, message_id, sender, subject, pole, "
            "category, urgency, sentiment, decision, suspicious, processed_at) VALUES "
            "('b@x', '<1@x>', 'c@x', 's', 'SOFT', 'client', 'normale', 'neutre', 'faq', "
            ":f, CURRENT_TIMESTAMP)"), {"f": False})
    migrate.upgrade(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT summary FROM processed_messages")).scalar() == ""
