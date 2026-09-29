"""Alertes de l'agent chef aux valideurs (section 12).

* Nouveau contenu à valider (niveau 2) : un récapitulatif par mail au valideur du pôle.
* Toujours pas validé après 24 h : relance au suppléant (le contenu n'est jamais publié
  par défaut).
* Dossier réservé à un humain (niveau 3) : alerte à la direction.

Les mails partent de la boîte `IBIG_NOTIFICATION_MAILBOX` ; sans elle, rien n'est envoyé
et les éléments restent à notifier (ils le seront dès qu'elle sera configurée).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import OrgConfig, Settings
from ..db import PendingAction, User, utcnow
from ..governance import ActionRequest, Governor

AGENT = "chef"


@dataclass
class NotifyResult:
    envoyes: int = 0
    elements: int = 0
    sans_destinataire: list[int] = field(default_factory=list)
    desactive: bool = False


class ValidatorNotifier:
    def __init__(self, settings: Settings, org: OrgConfig, governor: Governor,
                 session_factory: sessionmaker[Session], connected_mailboxes: set[str]) -> None:
        self.settings = settings
        self.connected_mailboxes = connected_mailboxes
        self.org = org
        self.gov = governor
        self._sessions = session_factory

    def _direction(self) -> list[str]:
        with self._sessions() as s:
            return list(s.scalars(select(User.email).where(
                User.role.in_(("direction", "admin")), User.active.is_(True))).all())

    def _recipients(self, pa: PendingAction, escalation: bool) -> list[str]:
        if pa.level >= 3:
            return self._direction()
        pole = self.org.pole(pa.pole)
        target = (pole.suppleant if escalation else pole.valideur) if pole else ""
        if escalation and pole and not target:
            target = pole.valideur
        return [target] if target else self._direction()

    def run(self, now: datetime | None = None) -> NotifyResult:
        now = now or utcnow()
        result = NotifyResult()
        mailbox = self.settings.notification_mailbox
        if not mailbox or mailbox not in self.connected_mailboxes:
            result.desactive = True
            return result

        # (destinataire, type) -> actions
        batches: dict[tuple[str, str], list[PendingAction]] = defaultdict(list)
        marks: dict[int, set[str]] = defaultdict(set)
        with self._sessions() as s:
            new = s.scalars(select(PendingAction).where(
                PendingAction.status.in_(("pending", "prepared")),
                PendingAction.notified_at.is_(None))).all()
            late = s.scalars(select(PendingAction).where(
                PendingAction.status == "pending", PendingAction.flagged_at.is_not(None),
                PendingAction.escalated_at.is_(None))).all()
        for kind, rows in (("nouveau", new), ("retard", late)):
            for pa in rows:
                recipients = self._recipients(pa, escalation=(kind == "retard"))
                if not recipients:
                    result.sans_destinataire.append(pa.id)
                    continue
                for r in recipients:
                    batches[(r.lower(), kind)].append(pa)
                marks[pa.id].add(kind)

        sent_ok: set[tuple[int, str]] = set()
        for (recipient, kind), items in batches.items():
            out = self.gov.submit(ActionRequest(
                agent=AGENT, action_type="notify.internal", channel="interne",
                account=mailbox,
                title=f"Alerte valideur ({kind}) : {len(items)} élément(s) → {recipient}",
                payload={"mailbox": mailbox, "to": recipient,
                         "subject": self._subject(kind, items),
                         "body": self._body(kind, items)},
            ))
            if out.status == "executed":
                result.envoyes += 1
                sent_ok.update((pa.id, kind) for pa in items)

        with self._sessions() as s:
            for pid, kind in sent_ok:
                pa = s.get(PendingAction, pid)
                if kind == "nouveau":
                    pa.notified_at = now
                else:
                    pa.escalated_at = now
            s.commit()
        result.elements = len({pid for pid, _ in sent_ok})
        return result

    @staticmethod
    def _subject(kind: str, items: list[PendingAction]) -> str:
        if kind == "retard":
            return f"[IBIG] {len(items)} validation(s) en attente depuis plus de 24 h"
        if all(pa.level >= 3 for pa in items):
            return f"[IBIG] {len(items)} dossier(s) réservé(s) à la direction"
        return f"[IBIG] {len(items)} élément(s) à valider"

    def _body(self, kind: str, items: list[PendingAction]) -> str:
        url = self.settings.dashboard_url.rstrip("/") + "/validations"
        intro = {
            "nouveau": "L'agent IA a préparé les éléments suivants :",
            "retard": ("Ces éléments attendent une validation depuis plus de 24 h. Vous êtes "
                       "suppléant du pôle : ils ne seront pas publiés sans décision."),
        }[kind]
        lines = ["Bonjour,", "", intro, ""]
        for pa in items:
            niveau = "à valider" if pa.level == 2 else "réservé à un humain"
            lines.append(f"- [{pa.pole or '—'}] {pa.title} ({niveau})")
        lines += ["", f"Tableau de bord : {url}", "",
                  "Message envoyé automatiquement par l'agent IA IBIG."]
        return "\n".join(lines)
