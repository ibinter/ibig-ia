"""Données personnelles (section 13) : conservation limitée, droit d'accès et d'effacement.

* Conservation : 12 mois par défaut (IBIG_RETENTION_MONTHS) pour le journal, les mails
  traités, les tickets résolus et les prospects clos sans activité.
* Droit d'accès : `ibig-agent contact exporter --email …` rassemble tout ce que l'agent
  détient sur une personne.
* Droit d'effacement : `ibig-agent contact supprimer --email …` efface ces données ; la
  trace de la suppression reste au journal, sans l'adresse.

Les mails eux-mêmes restent dans les boîtes (Gmail, LWS) : leur conservation relève de
la politique de messagerie d'IBIG, pas de l'agent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from .db import JournalEntry, PendingAction, ProcessedMessage, Prospect, Ticket, utcnow

CLOSED_PROSPECTS = ("perdu", "sans_suite")


@dataclass
class PurgeResult:
    journal: int = 0
    mails: int = 0
    tickets: int = 0
    prospects: int = 0
    validations: int = 0

    def __str__(self) -> str:
        return (f"supprimés : {self.journal} entrée(s) de journal, {self.mails} mail(s) "
                f"traité(s), {self.tickets} ticket(s), {self.prospects} prospect(s), "
                f"{self.validations} validation(s) closes")


def purge(sessions: sessionmaker[Session], months: int = 12,
          now: datetime | None = None) -> PurgeResult:
    limit = (now or utcnow()) - timedelta(days=round(months * 30.44))
    r = PurgeResult()
    with sessions() as s:
        r.journal = s.execute(delete(JournalEntry).where(
            JournalEntry.created_at < limit)).rowcount
        r.mails = s.execute(delete(ProcessedMessage).where(
            ProcessedMessage.processed_at < limit)).rowcount
        r.tickets = s.execute(delete(Ticket).where(
            Ticket.status == "resolu", Ticket.created_at < limit)).rowcount
        r.prospects = s.execute(delete(Prospect).where(
            Prospect.status.in_(CLOSED_PROSPECTS), Prospect.updated_at < limit)).rowcount
        r.validations = s.execute(delete(PendingAction).where(
            PendingAction.status.not_in(("pending", "prepared")),
            PendingAction.created_at < limit)).rowcount
        s.add(JournalEntry(agent="chef", action_type="privacy.purge", level=1,
                           channel="interne", status="executed",
                           summary=f"Purge des données de plus de {months} mois : {r}"))
        s.commit()
    return r


@dataclass
class ContactData:
    email: str
    prospect: dict | None = None
    mails: list[dict] = field(default_factory=list)
    tickets: list[dict] = field(default_factory=list)
    validations: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.prospect or self.mails or self.tickets or self.validations)


def _row(obj, fields: tuple[str, ...]) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = v.isoformat() if isinstance(v, datetime) else v
    return out


def _pending_for(s: Session, email: str) -> list[PendingAction]:
    rows = s.scalars(select(PendingAction).where(PendingAction.channel == "mail")).all()
    return [pa for pa in rows if (pa.payload.get("to") or "").lower() == email
            or (pa.payload.get("from") or "").lower() == email]


def export_contact(sessions: sessionmaker[Session], email: str) -> ContactData:
    email = email.strip().lower()
    data = ContactData(email)
    with sessions() as s:
        p = s.scalar(select(Prospect).where(Prospect.email == email))
        if p:
            data.prospect = _row(p, ("email", "name", "pole", "source", "need", "status",
                                     "score", "temperature", "solution", "created_at",
                                     "updated_at", "trial_ends_at", "demo_at"))
        data.mails = [_row(m, ("mailbox", "subject", "summary", "category", "pole",
                               "received_at", "processed_at"))
                      for m in s.scalars(select(ProcessedMessage).where(
                          ProcessedMessage.sender == email)).all()]
        data.tickets = [_row(t, ("id", "channel", "pole", "question", "status", "created_at"))
                        for t in s.scalars(select(Ticket).where(Ticket.contact == email)).all()]
        data.validations = [
            {"titre": pa.title, "statut": pa.status, "cree_le": pa.created_at.isoformat(),
             "texte": pa.payload.get("body", "")} for pa in _pending_for(s, email)]
    return data


def erase_contact(sessions: sessionmaker[Session], email: str, by: str) -> ContactData:
    """Efface les données d'une personne. Une validation en attente la concernant est
    rejetée : aucun message ne lui sera plus envoyé par ce biais."""
    email = email.strip().lower()
    found = export_contact(sessions, email)
    digest = hashlib.sha256(email.encode()).hexdigest()[:12]
    with sessions() as s:
        message_ids = list(s.scalars(select(ProcessedMessage.message_id).where(
            ProcessedMessage.sender == email)))
        s.execute(delete(Prospect).where(Prospect.email == email))
        s.execute(delete(ProcessedMessage).where(ProcessedMessage.sender == email))
        s.execute(delete(Ticket).where(Ticket.contact == email))
        for pa in _pending_for(s, email):
            if pa.status in ("pending", "prepared"):
                pa.status, pa.decided_by, pa.decided_at = "rejected", by, utcnow()
                pa.error = "données du contact effacées à sa demande"
            pa.payload = {"efface": True}
            pa.title = "[contenu effacé à la demande du contact]"
        # Journal : toute entrée qui cite la personne ou porte sur un de ses mails.
        refs = set(message_ids)
        for e in s.scalars(select(JournalEntry)).all():
            details = e.details or {}
            if (email in (e.summary or "").lower() or details.get("ref") in refs
                    or email in json.dumps(details, ensure_ascii=False).lower()):
                e.summary, e.details = "[effacé à la demande du contact]", {}
        s.add(JournalEntry(agent="humain", action_type="privacy.erase", level=3,
                           channel="interne", status="executed",
                           summary=f"Données d'un contact effacées (empreinte {digest})",
                           decided_by=by))
        s.commit()
    return found
