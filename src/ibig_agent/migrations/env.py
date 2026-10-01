"""Environnement Alembic : les migrations reçoivent une connexion de ibig_agent.migrate."""

from alembic import context

from ibig_agent.db import Base, include_object

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Lancer les migrations avec `ibig-agent migrer`")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # ALTER limités sous SQLite
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
