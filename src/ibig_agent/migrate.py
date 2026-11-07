"""Migrations du schéma de base (Alembic).

La base de production n'est jamais créée ni modifiée à la main : chaque évolution du
schéma est une migration versionnée dans src/ibig_agent/migrations/versions, appliquée
par `ibig-agent migrer` (et automatiquement au démarrage du service).

Pour créer une migration après avoir modifié db.py :
    ibig-agent migrer --nouvelle "ajout de ..."
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

SCRIPT_LOCATION = Path(__file__).parent / "migrations"


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as conn:
        if current_revision(engine) is None and inspect(conn).has_table("journal"):
            raise RuntimeError(
                "Base créée sans migrations (version de développement antérieure). "
                "La recréer, ou la marquer avec `ibig-agent migrer --marquer` si son "
                "schéma est déjà à jour."
            )
        cfg = _config()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def stamp(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as conn:
        cfg = _config()
        cfg.attributes["connection"] = conn
        command.stamp(cfg, revision)


def new_revision(engine: Engine, message: str) -> None:
    """Génère une migration en comparant db.py à une base à jour.

    Toujours relire le fichier : par exemple, une colonne NOT NULL ajoutée à une table
    existante doit recevoir un server_default, sinon la migration échoue en production.
    """
    with engine.begin() as conn:
        cfg = _config()
        cfg.attributes["connection"] = conn
        head = head_revision()
        rev_id = f"{int(head) + 1:04d}" if head and head.isdigit() else None
        command.revision(cfg, message=message, autogenerate=True, rev_id=rev_id)
