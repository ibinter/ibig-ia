"""Chiffres de la page d'accueil : cartes, courbe d'activité, répartition par pôle.

Tout est filtré sur les pôles d'un valideur (cloisonnement strict) ; la direction et
l'administration voient l'ensemble.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal
from ..db import (
    AIUsage,
    JobStatus,
    JournalEntry,
    PendingAction,
    ProcessedMessage,
    Prospect,
    ScheduledPost,
    Ticket,
    utcnow,
)

ACTIVITY_DAYS = 14


@dataclass
class DayCount:
    day: datetime
    count: int


@dataclass
class HomeStats:
    pending: int = 0
    prepared: int = 0
    mails_total: int = 0
    mails_24h: int = 0
    prospects: int = 0
    hot_prospects: int = 0
    open_tickets: int = 0
    suspicious_24h: int = 0
    ai_spend: float = 0.0
    ai_budget: float = 0.0
    activity: list[DayCount] = field(default_factory=list)
    by_pole: list[tuple[str, int]] = field(default_factory=list)
    decisions: list[tuple[str, int]] = field(default_factory=list)
    recent: list[JournalEntry] = field(default_factory=list)
    # À traiter en priorité, publications à venir, alertes de veille
    priority: list[PendingAction] = field(default_factory=list)
    upcoming: list[ScheduledPost] = field(default_factory=list)
    alerts: list[JournalEntry] = field(default_factory=list)

    @property
    def activity_max(self) -> int:
        return max((d.count for d in self.activity), default=0)

    @property
    def activity_total(self) -> int:
        return sum(d.count for d in self.activity)

    @property
    def by_pole_max(self) -> int:
        return max((n for _, n in self.by_pole), default=0)

    @property
    def ai_ratio(self) -> float:
        return min(self.ai_spend / self.ai_budget, 1.0) if self.ai_budget else 0.0


def _scope(q, column, who: Principal):
    return q.where(column.in_(who.poles)) if who.role == "valideur" else q


def home_stats(s: Session, who: Principal, ai_budget: float,
               now: datetime | None = None) -> HomeStats:
    now = now or utcnow()
    day_ago = now - timedelta(days=1)
    st = HomeStats(ai_budget=ai_budget)

    counts = dict(s.execute(_scope(
        select(PendingAction.status, func.count()).group_by(PendingAction.status),
        PendingAction.pole, who)).all())
    st.pending, st.prepared = counts.get("pending", 0), counts.get("prepared", 0)

    mails = _scope(select(func.count()).select_from(ProcessedMessage),
                   ProcessedMessage.pole, who)
    st.mails_total = s.scalar(mails) or 0
    st.mails_24h = s.scalar(mails.where(ProcessedMessage.processed_at >= day_ago)) or 0
    st.suspicious_24h = s.scalar(mails.where(ProcessedMessage.processed_at >= day_ago,
                                             ProcessedMessage.suspicious.is_(True))) or 0

    prospects = _scope(select(func.count()).select_from(Prospect), Prospect.pole, who)
    st.prospects = s.scalar(prospects) or 0
    st.hot_prospects = s.scalar(prospects.where(Prospect.temperature == "chaud")) or 0
    st.open_tickets = s.scalar(_scope(select(func.count()).select_from(Ticket)
                                      .where(Ticket.status == "ouvert"), Ticket.pole, who)) or 0

    # Dépense IA du mois : chiffre global, réservé à la direction.
    if who.role != "valideur":
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        st.ai_spend = float(s.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0))
                                     .where(AIUsage.created_at >= month)) or 0.0)

    # Activité : mails traités par jour sur 14 jours (regroupement en Python, portable
    # entre SQLite et PostgreSQL).
    start = (now - timedelta(days=ACTIVITY_DAYS - 1)).replace(hour=0, minute=0, second=0,
                                                              microsecond=0)
    stamps = s.scalars(_scope(select(ProcessedMessage.processed_at)
                              .where(ProcessedMessage.processed_at >= start),
                              ProcessedMessage.pole, who)).all()
    per_day: dict[str, int] = {}
    for ts in stamps:
        per_day[ts.strftime("%Y-%m-%d")] = per_day.get(ts.strftime("%Y-%m-%d"), 0) + 1
    st.activity = [DayCount(d, per_day.get(d.strftime("%Y-%m-%d"), 0))
                   for d in (start + timedelta(days=i) for i in range(ACTIVITY_DAYS))]

    month_ago = now - timedelta(days=30)
    st.by_pole = [(p or "—", n) for p, n in s.execute(_scope(
        select(ProcessedMessage.pole, func.count()).where(
            ProcessedMessage.processed_at >= month_ago).group_by(ProcessedMessage.pole)
        .order_by(func.count().desc()), ProcessedMessage.pole, who)).all()]
    st.decisions = [(d or "—", n) for d, n in s.execute(_scope(
        select(ProcessedMessage.decision, func.count()).where(
            ProcessedMessage.processed_at >= month_ago).group_by(ProcessedMessage.decision)
        .order_by(func.count().desc()), ProcessedMessage.pole, who)).all()]

    st.recent = list(s.scalars(_scope(select(JournalEntry).order_by(JournalEntry.id.desc()),
                                      JournalEntry.pole, who).limit(8)).all())

    # Priorités : validations en retard d'abord, puis les plus anciennes ; niveau 3 pour
    # la direction seulement.
    statuses = ("pending",) if who.role == "valideur" else ("pending", "prepared")
    items = s.scalars(_scope(select(PendingAction).where(PendingAction.status.in_(statuses)),
                             PendingAction.pole, who)).all()
    st.priority = sorted(items, key=lambda pa: (pa.flagged_at is None, pa.level != 3,
                                                pa.created_at))[:6]
    st.upcoming = list(s.scalars(_scope(select(ScheduledPost).where(
        ScheduledPost.status.in_(("programme", "echec"))).order_by(ScheduledPost.publish_at),
        ScheduledPost.pole, who).limit(5)).all())
    st.alerts = list(s.scalars(_scope(select(JournalEntry).where(
        JournalEntry.action_type == "veille.alert", JournalEntry.created_at >= now
        - timedelta(days=7)).order_by(JournalEntry.id.desc()), JournalEntry.pole, who)
        .limit(4)).all())
    return st


def nav_counts(s: Session, who: Principal) -> dict[str, int]:
    """Pastilles du menu : ce qui attend une action de cette personne."""
    q = select(PendingAction.pole, PendingAction.status, func.count()).where(
        PendingAction.status.in_(("pending", "prepared"))).group_by(
        PendingAction.pole, PendingAction.status)
    pending = prepared = 0
    for pole, status, n in s.execute(q).all():
        if status == "pending" and (who.role != "valideur" or pole in who.poles):
            pending += n
        elif status == "prepared" and who.can_handle_level3():
            prepared += n
    tickets = s.scalar(_scope(select(func.count()).select_from(Ticket)
                              .where(Ticket.status == "ouvert"), Ticket.pole, who)) or 0
    failing = 0
    if who.role != "valideur":
        failing = s.scalar(select(func.count()).select_from(JobStatus)
                           .where(JobStatus.failures > 0)) or 0
    return {"validations": pending + prepared, "tickets": tickets, "sante": failing}
