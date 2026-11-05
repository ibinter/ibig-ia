"""Console de maintenance : l'agent DEMANDE, la direction CONFIRME, le serveur EXÉCUTE (liste fermée d'actions).

Le service n'exécute jamais rien lui-même : il enregistre des demandes. Un petit programme du serveur
(deploy/console-actions.py) ne traite que les demandes confirmées, et seulement si (action, cible) figure
dans maintenance_catalog.CATALOG. Chaque demande est tracée : qui l'a demandée, qui l'a confirmée, le résultat.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import MaintenanceAction, utcnow
from .maintenance_catalog import CATALOG, command_for

EXPIRE_HOURS = 2           # une demande non confirmée au bout de 2 h est annulée
MAX_OPEN = 10              # au plus 10 demandes ouvertes à la fois


class MaintenanceService:
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def catalog(self) -> list[dict]:
        return [{"action": a, "label": e["label"],
                 "targets": [{"key": k, "label": t["label"], "sensible": t["sensible"]}
                             for k, t in e["targets"].items()]} for a, e in CATALOG.items()]

    def request(self, action: str, target: str, by: str) -> int:
        if command_for(action, target) is None:
            raise ValueError("Cette action n'est pas dans la liste autorisée")
        with self._sessions() as s:
            self._expire(s)
            open_rows = s.scalars(select(MaintenanceAction).where(
                MaintenanceAction.status.in_(("demandee", "confirmee", "en_cours")))).all()
            if len(open_rows) >= MAX_OPEN:
                raise ValueError("Trop de demandes en attente : attendez qu'elles se terminent")
            if any(r.action == action and r.target == target for r in open_rows):
                raise ValueError("Cette demande est déjà en cours")
            row = MaintenanceAction(action=action, target=target, requested_by=by)
            s.add(row)
            s.commit()
            return row.id

    def confirm(self, action_id: int, by: str) -> None:
        with self._sessions() as s:
            row = s.get(MaintenanceAction, action_id)
            if row is None or row.status != "demandee":
                raise ValueError("Demande introuvable ou déjà traitée")
            if utcnow() - _aware(row.created_at) > timedelta(hours=EXPIRE_HOURS):
                row.status, row.output, row.done_at = "annulee", "Expirée sans confirmation", utcnow()
                s.commit()
                raise ValueError("Demande expirée : refaites-la")
            if command_for(row.action, row.target) is None:
                row.status, row.output, row.done_at = "refusee", "Hors liste autorisée", utcnow()
                s.commit()
                raise ValueError("Cette action n'est plus autorisée")
            row.status, row.confirmed_by = "confirmee", by
            s.commit()

    def cancel(self, action_id: int, by: str) -> None:
        with self._sessions() as s:
            row = s.get(MaintenanceAction, action_id)
            if row is None or row.status not in ("demandee", "confirmee"):
                raise ValueError("Demande introuvable ou déjà lancée")
            row.status, row.output, row.done_at = "annulee", f"Annulée par {by}", utcnow()
            s.commit()

    def recent(self, limit: int = 25) -> list[MaintenanceAction]:
        with self._sessions() as s:
            self._expire(s)
            s.commit()
            return list(s.scalars(select(MaintenanceAction).order_by(MaintenanceAction.id.desc()).limit(limit)).all())

    @staticmethod
    def _expire(s: Session) -> None:
        limit = utcnow() - timedelta(hours=EXPIRE_HOURS)
        for row in s.scalars(select(MaintenanceAction).where(MaintenanceAction.status == "demandee")).all():
            if _aware(row.created_at) < limit:
                row.status, row.output, row.done_at = "annulee", "Expirée sans confirmation", utcnow()


def _aware(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=utcnow().tzinfo)
