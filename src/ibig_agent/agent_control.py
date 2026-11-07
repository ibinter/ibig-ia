"""Suspension et quotas des agents (cahier des charges, sections 4, 14 et 15).

* Suspendre un agent bloque ses NOUVELLES actions (le Governor les refuse, avec la raison
  au journal) ; ses actions déjà en attente de validation ne partent pas non plus tant qu'il
  est suspendu. Les journaux sont conservés.
* Quota quotidien : nombre d'actions par jour et coût d'IA par jour. Atteint, l'agent est
  bloqué jusqu'au lendemain ; la page Supervision le montre.

Le coût d'IA se rattache à un agent par la finalité de l'appel (« commercial.qualify » →
agent commercial).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .db import AgentControl, AIUsage, JournalEntry, utcnow

# Finalités d'appel d'IA (préfixe avant le premier point) -> agent
PURPOSE_AGENT = {
    "commercial": "commercial", "mail": "messagerie", "support": "support",
    "social": "communication", "campaign": "communication", "web": "contenus_web",
    "veille": "veille", "chef": "chef",
}
# Agents supervisés (ceux de la gouvernance, plus le futur Prospecteur du Lot 3)
AGENTS = ("chef", "messagerie", "support", "communication", "contenus_web", "commercial",
          "veille", "prospecteur")
LABELS = {
    "chef": "Chef d'orchestre", "messagerie": "Messagerie (mails, WhatsApp)",
    "support": "Support et SARA", "communication": "Communication et campagnes",
    "contenus_web": "Contenus web", "commercial": "Commercial", "veille": "Veille",
    "prospecteur": "Prospecteur (Lot 3)",
}
COUNTED = ("executed", "queued", "prepared")   # statuts de journal comptés comme « exécutions »


def purposes_of(agent: str) -> list[str]:
    return [p for p, a in PURPOSE_AGENT.items() if a == agent]


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class Usage:
    runs: int
    cost: float


def usage_today(sessions: sessionmaker[Session], agent: str,
                now: datetime | None = None) -> Usage:
    now = now or utcnow()
    start = day_start(now)
    with sessions() as s:
        runs = s.scalar(select(func.count()).select_from(JournalEntry).where(
            JournalEntry.agent == agent, JournalEntry.created_at >= start,
            JournalEntry.status.in_(COUNTED))) or 0
        prefixes = purposes_of(agent)
        cost = 0.0
        if prefixes:
            cost = s.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(
                AIUsage.created_at >= start,
                or_(*[AIUsage.purpose.like(f"{p}.%") for p in prefixes]))) or 0.0
    return Usage(int(runs), float(cost))


def control(sessions: sessionmaker[Session], agent: str) -> AgentControl | None:
    with sessions() as s:
        return s.get(AgentControl, agent)


def controls(sessions: sessionmaker[Session]) -> dict[str, AgentControl]:
    with sessions() as s:
        return {c.agent: c for c in s.scalars(select(AgentControl)).all()}


def is_suspended(sessions: sessionmaker[Session], agent: str) -> str:
    """Raison de la suspension, ou chaîne vide si l'agent n'est pas suspendu."""
    c = control(sessions, agent)
    if c is not None and c.suspended:
        return f"Agent suspendu{' : ' + c.reason if c.reason else ''}"
    return ""


def blocked_reason(sessions: sessionmaker[Session], agent: str,
                   now: datetime | None = None) -> str:
    """Pourquoi l'agent ne peut pas agir maintenant (suspension ou quota du jour), sinon ''."""
    c = control(sessions, agent)
    if c is None:
        return ""
    if c.suspended:
        return f"Agent suspendu{' : ' + c.reason if c.reason else ''}"
    if c.runs_cap_day is None and c.cost_cap_day is None:
        return ""
    use = usage_today(sessions, agent, now)
    if c.runs_cap_day is not None and use.runs >= c.runs_cap_day:
        return f"Quota quotidien atteint ({use.runs}/{c.runs_cap_day} actions)"
    if c.cost_cap_day is not None and use.cost >= c.cost_cap_day:
        return f"Plafond de coût quotidien atteint ({use.cost:.2f}/{c.cost_cap_day:.2f} USD)"
    return ""


def set_suspended(sessions: sessionmaker[Session], agent: str, suspended: bool, by: str,
                  reason: str = "") -> None:
    if agent not in AGENTS:
        raise ValueError(f"Agent inconnu : {agent}")
    with sessions() as s:
        c = s.get(AgentControl, agent) or AgentControl(agent=agent)
        c.suspended, c.reason = suspended, reason.strip()[:300]
        c.updated_by, c.updated_at = by, utcnow()
        s.add(c)
        s.add(JournalEntry(agent="humain",
                           action_type="agent.suspendu" if suspended else "agent.repris",
                           level=3, channel="interne", status="executed",
                           summary=f"Agent {agent} {'suspendu' if suspended else 'repris'}",
                           details={"agent": agent, "raison": reason.strip()}, decided_by=by))
        s.commit()


def set_quotas(sessions: sessionmaker[Session], agent: str, runs: int | None,
               cost: float | None, by: str) -> None:
    """Plafonds quotidiens (None = pas de limite). Un plafond doit être positif."""
    if agent not in AGENTS:
        raise ValueError(f"Agent inconnu : {agent}")
    if (runs is not None and runs <= 0) or (cost is not None and cost <= 0):
        raise ValueError("Un plafond doit être un nombre positif (ou vide pour aucune limite)")
    with sessions() as s:
        c = s.get(AgentControl, agent) or AgentControl(agent=agent)
        c.runs_cap_day, c.cost_cap_day = runs, cost
        c.updated_by, c.updated_at = by, utcnow()
        s.add(c)
        s.add(JournalEntry(agent="humain", action_type="agent.quota", level=3, channel="interne",
                           status="executed", summary=f"Quotas de l'agent {agent} modifiés",
                           details={"agent": agent, "actions_par_jour": runs,
                                    "cout_par_jour_usd": cost}, decided_by=by))
        s.commit()
