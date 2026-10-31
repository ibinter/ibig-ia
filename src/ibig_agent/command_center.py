"""Centre de commande (cahier des charges v2, section 9) : la vue du dirigeant, en lecture seule.

Tout vient des tables existantes ; rien n'est écrit ici. Les chiffres sont ceux d'aujourd'hui
(heure locale du serveur) sauf mention contraire.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import agent_control
from .db import (
    AIUsage,
    JobStatus,
    JournalEntry,
    Mission,
    MissionTask,
    Payment,
    PendingAction,
    Prospect,
    Quote,
    Ticket,
    utcnow,
)

LOST = ("gagne", "perdu", "sans_suite", "paye_livre", "injoignable", "invalide", "exclu")
FAILED_STATUSES = ("failed", "blocked", "flagged")


def _agent_of(purpose: str) -> str:
    return agent_control.PURPOSE_AGENT.get((purpose or "").split(".")[0], "autre")


def build(sessions: sessionmaker[Session], pole_codes: list[str]) -> dict:
    now = utcnow()
    today = agent_control.day_start(now)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with sessions() as s:
        def count(model, *where) -> int:
            return int(s.scalar(select(func.count()).select_from(model).where(*where)) or 0)

        # ---------------------------------------------------------------- résumé du jour
        summary = {
            "missions_running": count(Mission, Mission.status == "en_cours"),
            "missions_done_today": count(Mission, Mission.status == "termine",
                                         Mission.ended_at >= today),
            "tasks_ready": count(MissionTask, MissionTask.status.in_(("prete", "en_cours"))),
            "tasks_blocked": count(MissionTask, MissionTask.status.in_(("bloquee", "echec"))),
            "decisions": count(PendingAction, PendingAction.status.in_(("pending", "prepared"))),
            "tickets": count(Ticket, Ticket.status == "ouvert"),
            "incidents": count(JournalEntry, JournalEntry.created_at >= today,
                               JournalEntry.status.in_(FAILED_STATUSES)),
        }

        # ---------------------------------------------------------------- agents
        agents = []
        controls = agent_control.controls(sessions)
        for agent in agent_control.AGENTS:
            last = s.scalars(select(JournalEntry).where(JournalEntry.agent == agent).order_by(
                JournalEntry.id.desc()).limit(1)).first()
            c = controls.get(agent)
            use = agent_control.usage_today(sessions, agent)
            agents.append({"agent": agent, "label": agent_control.LABELS[agent],
                           "suspended": bool(c and c.suspended), "runs": use.runs,
                           "cost": use.cost, "last": last})

        # ---------------------------------------------------------------- départements
        pend = dict(s.execute(select(PendingAction.pole, func.count()).where(
            PendingAction.status.in_(("pending", "prepared"))).group_by(PendingAction.pole)).all())
        open_prospects = dict(s.execute(select(Prospect.pole, func.count()).where(
            Prospect.status.notin_(LOST)).group_by(Prospect.pole)).all())
        tickets = dict(s.execute(select(Ticket.pole, func.count()).where(
            Ticket.status == "ouvert").group_by(Ticket.pole)).all())
        missions = dict(s.execute(select(Mission.pole, func.count()).where(
            Mission.status == "en_cours").group_by(Mission.pole)).all())
        quotes = dict(s.execute(select(Quote.pole, func.count()).where(
            Quote.status == "envoye").group_by(Quote.pole)).all())
        revenue = dict(s.execute(select(Payment.pole, func.coalesce(func.sum(Payment.amount), 0))
                                 .where(Payment.status == "confirme",
                                        Payment.confirmed_at >= month)
                                 .group_by(Payment.pole)).all())
        departments = [{
            "pole": pole, "decisions": pend.get(pole, 0),
            "prospects": open_prospects.get(pole, 0), "tickets": tickets.get(pole, 0),
            "missions": missions.get(pole, 0), "quotes": quotes.get(pole, 0),
            "revenue": int(revenue.get(pole, 0))} for pole in pole_codes]

        # ---------------------------------------------------------------- commercial
        funnel = dict(s.execute(select(Prospect.status, func.count()).group_by(
            Prospect.status)).all())
        commercial = {
            "found": sum(funnel.values()),
            "qualified": sum(funnel.get(k, 0) for k in (
                "qualifie", "en_discussion", "relance", "interesse")),
            "quotes_sent": count(Quote, Quote.status.in_(("envoye", "accepte", "paye"))),
            "won": funnel.get("gagne", 0) + funnel.get("paye_livre", 0),
            "collected": int(s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "confirme", Payment.confirmed_at >= month)) or 0),
            "to_confirm": count(Payment, Payment.status == "a_confirmer"),
        }

        # ---------------------------------------------------------------- incidents
        incidents = list(s.scalars(select(JournalEntry).where(
            JournalEntry.created_at >= now - timedelta(days=3),
            JournalEntry.status.in_(FAILED_STATUSES)).order_by(
            JournalEntry.id.desc()).limit(8)).all())
        failing_jobs = list(s.scalars(select(JobStatus).where(JobStatus.failures > 0)).all())

        # ---------------------------------------------------------------- coûts IA du mois
        by_model: dict[str, float] = {}
        by_agent: dict[str, float] = {}
        total = 0.0
        for model, purpose, cost in s.execute(select(
                AIUsage.model, AIUsage.purpose, AIUsage.cost_usd).where(
                AIUsage.created_at >= month)).all():
            by_model[model] = by_model.get(model, 0.0) + cost
            agent = _agent_of(purpose)
            by_agent[agent] = by_agent.get(agent, 0.0) + cost
            total += cost
        costs = {"total": total,
                 "by_model": sorted(by_model.items(), key=lambda kv: -kv[1]),
                 "by_agent": sorted(by_agent.items(), key=lambda kv: -kv[1])}

    return {"summary": summary, "agents": agents, "departments": departments,
            "commercial": commercial, "incidents": incidents, "failing_jobs": failing_jobs,
            "costs": costs}
