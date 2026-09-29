"""Règles de validation et gouvernance (section 12 du cahier des charges).

Toute action d'un agent passe par le `Governor` :

* niveau 1 — automatique : exécutée tout de suite, tracée au journal ;
* niveau 2 — validation en un clic : mise en file, exécutée seulement après validation ;
* niveau 3 — toujours humain : l'agent prépare, un humain décide et agit.

Le niveau d'une action vient de son type, jamais de l'agent qui la demande. Un agent
peut durcir le niveau (escalade) mais jamais l'assouplir. Un type inconnu tombe au
niveau 3 (« en cas de doute, le niveau le plus strict »).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import ChannelState, JournalEntry, PendingAction, utcnow


class Level(IntEnum):
    AUTOMATIQUE = 1
    VALIDATION = 2
    HUMAIN = 3


# Niveau de base de chaque type d'action.
ACTION_LEVELS: dict[str, Level] = {
    # Niveau 1 — automatique
    "mail.label": Level.AUTOMATIQUE,
    "mail.ack": Level.AUTOMATIQUE,
    "mail.faq_reply": Level.AUTOMATIQUE,
    "mail.forward_internal": Level.AUTOMATIQUE,
    "report.publish": Level.AUTOMATIQUE,
    "stats.read": Level.AUTOMATIQUE,
    "social.schedule_approved": Level.AUTOMATIQUE,
    # Niveau 2 — validation en un clic
    "mail.reply": Level.VALIDATION,
    "social.post": Level.VALIDATION,
    "social.manual_post": Level.VALIDATION,
    "web.article_draft": Level.VALIDATION,
    "commercial.followup": Level.VALIDATION,
    "campaign.mail": Level.VALIDATION,
    "campaign.whatsapp": Level.VALIDATION,
    "whatsapp.reply": Level.VALIDATION,
    # Niveau 3 — toujours humain
    "pricing.unpublished": Level.HUMAIN,
    "discount": Level.HUMAIN,
    "refund": Level.HUMAIN,
    "payment": Level.HUMAIN,
    "contract": Level.HUMAIN,
    "legal": Level.HUMAIN,
    "complaint.serious": Level.HUMAIN,
    "media.reply": Level.HUMAIN,
    "crisis": Level.HUMAIN,
    "security.suspicious_message": Level.HUMAIN,
}

# Autonomie maximale de chaque agent (section 6). Aucun agent ne la dépasse,
# même si une consigne le lui demande.
#   aucune      : coordonne, n'exécute rien lui-même (agent chef)
#   lecture     : lecture seule, rapports et statistiques (veille)
#   validation  : tout passe au minimum par la validation en un clic
#   automatique : peut exécuter les actions de niveau 1
AGENT_AUTONOMY: dict[str, str] = {
    "chef": "aucune",
    "veille": "lecture",
    "communication": "validation",
    "contenus_web": "validation",
    "commercial": "validation",
    "messagerie": "automatique",
    "support": "automatique",
}

# Actions sans effet extérieur, permises aux agents « aucune » et « lecture ».
READ_ONLY_ACTIONS = {"report.publish", "stats.read"}

CHANNELS = [
    "mail",
    "whatsapp",
    "facebook",
    "instagram",
    "threads",
    "linkedin",
    "tiktok",
    "x",
    "web",
]
ALL_CHANNELS = "*"


def level_for(action_type: str) -> Level:
    return ACTION_LEVELS.get(action_type, Level.HUMAIN)


class GovernanceError(Exception):
    pass


class ChannelStopped(GovernanceError):
    pass


@dataclass
class ActionRequest:
    agent: str
    action_type: str
    channel: str
    title: str
    payload: dict = field(default_factory=dict)
    account: str = ""
    pole: str = ""
    escalate_to: Level | None = None  # un agent peut durcir, jamais assouplir

    @property
    def level(self) -> Level:
        base = level_for(self.action_type)
        if self.escalate_to is not None and self.escalate_to > base:
            return self.escalate_to
        return base


@dataclass
class Outcome:
    status: str  # executed | queued | prepared | blocked | failed
    level: Level
    pending_id: int | None = None
    result: dict | None = None
    error: str = ""


Executor = Callable[[dict], dict | None]


def as_utc(dt: datetime) -> datetime:
    # SQLite rend des dates naïves : on les considère en UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Governor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        executors: dict[str, Executor] | None = None,
        approval_timeout: timedelta = timedelta(hours=24),
    ) -> None:
        self._sessions = session_factory
        self.executors: dict[str, Executor] = dict(executors or {})
        self.approval_timeout = approval_timeout

    # ------------------------------------------------------------------ arrêt
    def is_stopped(self, channel: str) -> bool:
        with self._sessions() as s:
            rows = s.scalars(
                select(ChannelState).where(ChannelState.channel.in_([channel, ALL_CHANNELS]))
            ).all()
            return any(r.stopped for r in rows)

    def set_stopped(self, channel: str, stopped: bool, by: str, reason: str = "") -> None:
        with self._sessions() as s:
            state = s.get(ChannelState, channel) or ChannelState(channel=channel)
            state.stopped = stopped
            state.reason = reason
            state.updated_by = by
            state.updated_at = utcnow()
            s.add(state)
            s.add(
                JournalEntry(
                    agent="humain",
                    action_type="killswitch.stop" if stopped else "killswitch.resume",
                    level=Level.HUMAIN,
                    channel=channel,
                    status="executed",
                    summary=f"Canal {channel} {'suspendu' if stopped else 'réactivé'}",
                    details={"reason": reason},
                    decided_by=by,
                )
            )
            s.commit()

    def channel_states(self) -> dict[str, ChannelState]:
        with self._sessions() as s:
            return {r.channel: r for r in s.scalars(select(ChannelState)).all()}

    # --------------------------------------------------------------- soumission
    def submit(self, req: ActionRequest) -> Outcome:
        level = req.level
        autonomy = AGENT_AUTONOMY.get(req.agent, "aucune")

        if autonomy in ("aucune", "lecture"):
            if req.action_type not in READ_ONLY_ACTIONS:
                raise GovernanceError(
                    f"L'agent {req.agent!r} ne peut pas émettre {req.action_type!r}"
                )
        elif autonomy == "validation" and level == Level.AUTOMATIQUE:
            level = Level.VALIDATION

        if level == Level.AUTOMATIQUE:
            return self._run_automatic(req, level)
        if level == Level.VALIDATION:
            return self._enqueue(req, level, status="pending")
        return self._enqueue(req, level, status="prepared")

    def _run_automatic(self, req: ActionRequest, level: Level) -> Outcome:
        if self.is_stopped(req.channel):
            self._journal(req, level, "blocked", "Canal suspendu : action non exécutée")
            return Outcome("blocked", level)
        try:
            result = self._execute(req.action_type, req.payload)
        except Exception as exc:  # noqa: BLE001 — tout échec est journalisé
            self._journal(req, level, "failed", f"Échec : {exc}")
            return Outcome("failed", level, error=str(exc))
        self._journal(req, level, "executed", req.title, result)
        return Outcome("executed", level, result=result)

    def _enqueue(self, req: ActionRequest, level: Level, status: str) -> Outcome:
        with self._sessions() as s:
            pa = PendingAction(
                agent=req.agent,
                action_type=req.action_type,
                level=int(level),
                channel=req.channel,
                account=req.account,
                pole=req.pole,
                title=req.title,
                payload=req.payload,
                status=status,
            )
            s.add(pa)
            s.flush()
            s.add(self._entry(req, level, "queued" if status == "pending" else "prepared", req.title,
                              {"pending_id": pa.id}))
            s.commit()
            return Outcome("queued" if status == "pending" else "prepared", level, pending_id=pa.id)

    # ---------------------------------------------------------------- décisions
    def approve(self, pending_id: int, by: str, payload_override: dict | None = None) -> Outcome:
        if not by:
            raise GovernanceError("La validation doit être nominative")
        with self._sessions() as s:
            pa = s.get(PendingAction, pending_id)
            if pa is None:
                raise GovernanceError(f"Action {pending_id} introuvable")
            if pa.level >= Level.HUMAIN:
                raise GovernanceError("Action de niveau 3 : elle doit être réalisée par un humain")
            if pa.status != "pending":
                raise GovernanceError(f"Action {pending_id} déjà traitée ({pa.status})")
            if self.is_stopped(pa.channel):
                raise ChannelStopped(f"Canal {pa.channel} suspendu")
            if payload_override:
                pa.payload = {**pa.payload, **payload_override}
            pa.status = "approved"
            pa.decided_by = by
            pa.decided_at = utcnow()
            s.commit()
            action_type, payload = pa.action_type, dict(pa.payload)

        try:
            result = self._execute(action_type, payload)
        except Exception as exc:  # noqa: BLE001
            self._close(pending_id, "failed", by, error=str(exc))
            return Outcome("failed", Level.VALIDATION, pending_id=pending_id, error=str(exc))
        self._close(pending_id, "executed", by, result=result)
        return Outcome("executed", Level.VALIDATION, pending_id=pending_id, result=result)

    def reject(self, pending_id: int, by: str, reason: str = "") -> None:
        self._decide_without_execution(pending_id, by, "rejected", reason)

    def mark_handled(self, pending_id: int, by: str, note: str = "") -> None:
        """Niveau 3 : un humain indique qu'il a traité le dossier préparé par l'agent."""
        self._decide_without_execution(pending_id, by, "handled", note)

    def _decide_without_execution(self, pending_id: int, by: str, status: str, note: str) -> None:
        if not by:
            raise GovernanceError("La décision doit être nominative")
        with self._sessions() as s:
            pa = s.get(PendingAction, pending_id)
            if pa is None:
                raise GovernanceError(f"Action {pending_id} introuvable")
            if pa.status not in ("pending", "prepared"):
                raise GovernanceError(f"Action {pending_id} déjà traitée ({pa.status})")
            pa.status = status
            pa.decided_by = by
            pa.decided_at = utcnow()
            pa.error = note
            s.add(
                JournalEntry(
                    agent=pa.agent,
                    action_type=pa.action_type,
                    level=pa.level,
                    channel=pa.channel,
                    account=pa.account,
                    pole=pa.pole,
                    status=status,
                    summary=pa.title,
                    details={"pending_id": pa.id, "note": note},
                    decided_by=by,
                )
            )
            s.commit()

    def flag_stale(self, now: datetime | None = None) -> list[PendingAction]:
        """Contenus non validés sous 24 h : signalés, jamais publiés par défaut."""
        now = now or utcnow()
        flagged: list[PendingAction] = []
        with self._sessions() as s:
            rows = s.scalars(
                select(PendingAction).where(
                    PendingAction.status == "pending", PendingAction.flagged_at.is_(None)
                )
            ).all()
            for pa in rows:
                if now - as_utc(pa.created_at) >= self.approval_timeout:
                    pa.flagged_at = now
                    flagged.append(pa)
                    s.add(
                        JournalEntry(
                            agent="chef",
                            action_type="approval.stale",
                            level=pa.level,
                            channel=pa.channel,
                            pole=pa.pole,
                            status="flagged",
                            summary=f"Non validé depuis 24 h : {pa.title}",
                            details={"pending_id": pa.id},
                        )
                    )
            s.commit()
        return flagged

    # ---------------------------------------------------------------- internes
    def _execute(self, action_type: str, payload: dict) -> dict | None:
        executor = self.executors.get(action_type)
        if executor is None:
            raise GovernanceError(f"Aucun connecteur configuré pour {action_type!r}")
        return executor(payload)

    def _close(self, pending_id: int, status: str, by: str, result: dict | None = None,
               error: str = "") -> None:
        with self._sessions() as s:
            pa = s.get(PendingAction, pending_id)
            pa.status = status
            pa.error = error
            s.add(
                JournalEntry(
                    agent=pa.agent,
                    action_type=pa.action_type,
                    level=pa.level,
                    channel=pa.channel,
                    account=pa.account,
                    pole=pa.pole,
                    status=status,
                    summary=pa.title if not error else f"{pa.title} — échec : {error}",
                    details={"pending_id": pa.id, "result": result or {}},
                    decided_by=by,
                )
            )
            s.commit()

    def _entry(self, req: ActionRequest, level: Level, status: str, summary: str,
               details: dict | None = None) -> JournalEntry:
        return JournalEntry(
            agent=req.agent,
            action_type=req.action_type,
            level=int(level),
            channel=req.channel,
            account=req.account,
            pole=req.pole,
            status=status,
            summary=summary,
            details=details or {},
        )

    def _journal(self, req: ActionRequest, level: Level, status: str, summary: str,
                 details: dict | None = None) -> None:
        with self._sessions() as s:
            s.add(self._entry(req, level, status, summary, details))
            s.commit()
