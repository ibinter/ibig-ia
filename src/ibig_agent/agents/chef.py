"""Agent chef : planifier, répartir, contrôler, rendre compte (section 6).

Il n'exécute rien lui-même : il lit l'état, signale les validations en retard et
publie le rapport quotidien (synthèse de 8 h, section 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..db import JournalEntry, PendingAction, ProcessedMessage, utcnow
from ..governance import ActionRequest, Governor
from ..llm import ClaudeClient


@dataclass
class DailyReport:
    since: datetime
    until: datetime
    mails_recus: int = 0
    mails_par_decision: dict[str, int] = field(default_factory=dict)
    mails_par_pole: dict[str, int] = field(default_factory=dict)
    urgences: list[str] = field(default_factory=list)
    suspects: int = 0
    validations_en_attente: int = 0
    validations_en_retard: int = 0
    dossiers_humains: int = 0
    echecs: int = 0
    depense_ia_mois_usd: float | None = None

    def as_text(self) -> str:
        traites = sum(v for k, v in self.mails_par_decision.items() if k != "en_cours")
        lines = [
            f"Rapport quotidien IBIG — {self.until:%d/%m/%Y %H:%M} (UTC)",
            "",
            f"Mails reçus (24 h) : {self.mails_recus} — traités : {traites}",
            "Par décision : " + (", ".join(f"{k} {v}" for k, v in
                                          sorted(self.mails_par_decision.items())) or "—"),
            "Par pôle : " + (", ".join(f"{k} {v}" for k, v in
                                      sorted(self.mails_par_pole.items())) or "—"),
            f"Mails suspects signalés : {self.suspects}",
            f"Validations en attente : {self.validations_en_attente} "
            f"(dont {self.validations_en_retard} de plus de 24 h)",
            f"Dossiers réservés à un humain (niveau 3) : {self.dossiers_humains}",
            f"Actions en échec (24 h) : {self.echecs}",
        ]
        if self.depense_ia_mois_usd is not None:
            lines.append(f"Dépense IA du mois : {self.depense_ia_mois_usd:.2f} USD")
        if self.urgences:
            lines += ["", "Urgences :"] + [f"- {u}" for u in self.urgences]
        return "\n".join(lines)


class ChefAgent:
    name = "chef"

    def __init__(self, governor: Governor, session_factory: sessionmaker[Session],
                 llm: ClaudeClient | None = None) -> None:
        self.gov = governor
        self._sessions = session_factory
        self.llm = llm

    def build_daily_report(self, now: datetime | None = None) -> DailyReport:
        now = now or utcnow()
        since = now - timedelta(hours=24)
        rep = DailyReport(since=since, until=now)
        with self._sessions() as s:
            recent = ProcessedMessage.processed_at >= since
            rep.mails_recus = s.scalar(
                select(func.count()).select_from(ProcessedMessage).where(recent)) or 0
            rep.mails_par_decision = dict(s.execute(
                select(ProcessedMessage.decision, func.count()).where(recent)
                .group_by(ProcessedMessage.decision)).all())
            rep.mails_par_pole = {k or "—": v for k, v in s.execute(
                select(ProcessedMessage.pole, func.count()).where(recent)
                .group_by(ProcessedMessage.pole)).all()}
            rep.suspects = s.scalar(select(func.count()).select_from(ProcessedMessage).where(
                recent, ProcessedMessage.suspicious.is_(True))) or 0
            rep.urgences = [
                f"{m.subject} ({m.sender}, {m.pole})" for m in s.scalars(
                    select(ProcessedMessage).where(recent, ProcessedMessage.urgency == "haute"))
            ]
            rep.validations_en_attente = s.scalar(select(func.count()).select_from(
                PendingAction).where(PendingAction.status == "pending")) or 0
            rep.validations_en_retard = s.scalar(select(func.count()).select_from(
                PendingAction).where(PendingAction.status == "pending",
                                     PendingAction.flagged_at.is_not(None))) or 0
            rep.dossiers_humains = s.scalar(select(func.count()).select_from(
                PendingAction).where(PendingAction.status == "prepared")) or 0
            rep.echecs = s.scalar(select(func.count()).select_from(JournalEntry).where(
                JournalEntry.created_at >= since, JournalEntry.status == "failed")) or 0
        if self.llm is not None:
            rep.depense_ia_mois_usd = self.llm.month_spend(now)
        return rep

    def run_daily(self, now: datetime | None = None) -> DailyReport:
        self.gov.flag_stale(now)
        report = self.build_daily_report(now)
        self.gov.submit(ActionRequest(
            agent=self.name, action_type="report.publish", channel="rapport",
            title=f"Rapport quotidien du {report.until:%d/%m/%Y}",
            payload={"text": report.as_text()},
        ))
        return report
