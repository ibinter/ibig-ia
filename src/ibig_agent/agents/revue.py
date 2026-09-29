"""Revue mensuelle (section 12) : erreurs, plaintes, indicateurs, ajustement des règles.

Produite le 1er de chaque mois sur le mois écoulé, publiée au tableau de bord et envoyée
à la direction. Les « pistes » sont des suggestions : c'est la revue humaine qui décide
d'ajouter une FAQ, de valider un guide ou de changer une règle.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db import JournalEntry, PendingAction, ProcessedMessage, Ticket, User, utcnow
from ..governance import ActionRequest, Governor
from .veille import VeilleAgent

AGENT = "chef"
STOP = {"les", "des", "une", "est", "pour", "par", "sur", "dans", "que", "qui", "avec",
        "demande", "souhaite", "comment", "votre", "vous", "nous", "plus", "sont", "aux"}


def _theme(text: str) -> str:
    """Clé grossière d'un sujet : les 3 mots significatifs les plus longs, triés."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    words = [w for w in re.findall(r"[a-z0-9]{4,}", text) if w not in STOP]
    return " ".join(sorted(sorted(set(words), key=len, reverse=True)[:3]))


@dataclass
class MonthlyReview:
    debut: datetime
    fin: datetime
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    pistes: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [(f"Revue mensuelle de l'agent IA IBIG — du {self.debut:%d/%m/%Y} au "
                  f"{self.fin:%d/%m/%Y}"), ""]
        for title, items in self.sections:
            lines += [title, *(f"- {i}" for i in items or ["rien à signaler"]), ""]
        lines += ["Pistes pour la revue (à décider par l'équipe)"]
        lines += [f"- {p}" for p in self.pistes or ["aucune"]]
        return "\n".join(lines)


class MonthlyReviewer:
    def __init__(self, governor: Governor, session_factory: sessionmaker[Session],
                 veille: VeilleAgent, notification_mailbox: str = "",
                 month_spend=None) -> None:
        self.gov = governor
        self._sessions = session_factory
        self.veille = veille
        self.notification_mailbox = notification_mailbox
        self.month_spend = month_spend

    def build(self, now: datetime | None = None, days: int = 30) -> MonthlyReview:
        now = now or utcnow()
        start = now - timedelta(days=days)
        review = MonthlyReview(start, now)
        with self._sessions() as s:
            journal = s.scalars(select(JournalEntry).where(
                JournalEntry.created_at >= start)).all()
            decided = s.scalars(select(PendingAction).where(
                PendingAction.decided_at >= start)).all()
            mails = s.scalars(select(ProcessedMessage).where(
                ProcessedMessage.processed_at >= start)).all()
            tickets = s.scalars(select(Ticket).where(Ticket.created_at >= start)).all()

        # 1. Indicateurs (section 3)
        ind = self.veille.indicators(days, now)
        review.sections.append(("Indicateurs", [
            f"{'✔' if i.etat == 'ok' else '✖' if i.etat == 'alerte' else '…'} {i.nom} : "
            f"{i.valeur} (cible {i.cible})" for i in ind.items]))

        # 2. Erreurs : actions en échec
        failed = Counter(e.action_type for e in journal if e.status == "failed")
        samples = {e.action_type: e.summary for e in journal if e.status == "failed"}
        review.sections.append(("Erreurs (actions en échec)", [
            f"{a} : {n} — ex. « {samples[a][:100]} »" for a, n in failed.most_common(10)]))

        # 3. Plaintes et dossiers humains
        negative = Counter(m.pole or "—" for m in mails if m.sentiment == "negatif"
                           and m.category != "spam")
        serious = Counter(e.action_type for e in journal
                          if e.action_type in ("legal", "complaint.serious")
                          and e.status == "prepared")
        suspicious = sum(m.suspicious for m in mails)
        complaints = []
        if negative:
            complaints.append("messages mécontents par pôle : "
                              + ", ".join(f"{k} {v}" for k, v in negative.most_common()))
        if serious:
            complaints.append(f"dossiers juridiques : {serious['legal']}, réclamations "
                              f"graves : {serious['complaint.serious']}")
        complaints += [f"messages piégés signalés : {suspicious}"] if suspicious else []
        review.sections.append(("Plaintes et incidents", complaints))

        # 4. Validations par type d'action
        stats: dict[str, Counter] = defaultdict(Counter)
        for pa in decided:
            stats[pa.action_type][pa.status] += 1
            if pa.payload.get("modifie_par_valideur"):
                stats[pa.action_type]["modifie"] += 1
        lines = []
        for action, c in sorted(stats.items()):
            total = sum(c[k] for k in ("executed", "failed", "rejected", "handled", "approved"))
            if not total:
                continue
            lines.append(f"{action} : {total} décision(s), {c['rejected']} rejet(s), "
                         f"{c['modifie']} modifiée(s) avant envoi, {c['failed']} échec(s)")
            if total >= 5 and c["rejected"] / total > 0.3:
                review.pistes.append(
                    f"{action} : {c['rejected']} rejets sur {total} — revoir les consignes "
                    "ou la base de connaissances de ce type de contenu")
            accepted = c["executed"] - c["modifie"]
            if action == "mail.reply" and total >= 20 and accepted / total >= 0.9:
                review.pistes.append(
                    f"mail.reply : {accepted}/{total} brouillons acceptés sans modification — "
                    "les questions récurrentes peuvent devenir des FAQ (réponse automatique)")
        review.sections.append(("Validations humaines", lines))

        # 5. Questions récurrentes sans réponse automatique → FAQ ou guide à écrire
        themes = Counter(_theme(m.summary) for m in mails
                         if m.category in ("support", "client") and m.decision == "brouillon"
                         and m.summary)
        themes.update(_theme(t.question) for t in tickets)
        for theme, n in themes.most_common(5):
            if theme and n >= 3:
                review.pistes.append(f"sujet récurrent ({n} fois) sans réponse automatique : "
                                     f"« {theme} » — ajouter une FAQ ou un guide")
        open_tickets = sum(t.status == "ouvert" for t in tickets)
        if open_tickets:
            review.pistes.append(f"{open_tickets} ticket(s) du support encore ouvert(s)")

        # 6. Coûts
        if self.month_spend is not None:
            review.sections.append(("Coûts de l'IA", [
                f"dépense du mois en cours : {self.month_spend(now):.2f} USD"]))
        return review

    def run(self, now: datetime | None = None) -> MonthlyReview:
        review = self.build(now)
        text = review.as_text()
        self.gov.submit(ActionRequest(
            agent=AGENT, action_type="report.publish", channel="rapport",
            title=f"Revue mensuelle au {review.fin:%d/%m/%Y}", payload={"text": text}))
        if self.notification_mailbox:
            with self._sessions() as s:
                recipients = s.scalars(select(User.email).where(
                    User.role.in_(("direction", "admin")), User.active.is_(True))).all()
            for to in recipients:
                self.gov.submit(ActionRequest(
                    agent=AGENT, action_type="notify.internal", channel="interne",
                    account=self.notification_mailbox, title=f"Revue mensuelle → {to}",
                    payload={"mailbox": self.notification_mailbox, "to": to,
                             "subject": f"[IBIG] Revue mensuelle au {review.fin:%d/%m/%Y}",
                             "body": text}))
        return review
