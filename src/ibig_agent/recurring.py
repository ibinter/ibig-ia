"""Travaux récurrents (cahier des charges v2, 5.1 et phase 5) : relancer un workflow approuvé à
chaque échéance (chaque jour, chaque semaine, chaque mois).

Garde-fous :
  - seul un workflow APPROUVÉ se lance, toujours dans sa dernière version approuvée ;
  - une échéance ne se lance qu'une fois par période (jour, semaine ISO, mois), même si le serveur
    redémarre ; une échéance manquée se rattrape dans la même période ;
  - si la mission de la période précédente n'est pas terminée, on ne lance pas la suivante
    (pas d'empilement) et le journal en garde la trace ;
  - arrêt général ou arrêt du département : rien ne se lance ;
  - les agents ne préparent des brouillons que si la récurrence le demande, avec un budget par
    mission (0,50 $ par défaut) en plus du plafond mensuel ; rien n'est jamais envoyé ni publié.

Les récurrences sont stockées dans les réglages (clé `recurring_jobs`, liste JSON) : aucune
migration. Désactiver ou supprimer une récurrence n'efface pas les missions déjà créées.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .configstore import service_value, set_service_value
from .db import JournalEntry, Mission, utcnow
from .governance import ALL_CHANNELS, Governor

KEY = "recurring_jobs"
CADENCES = {"jour": "Chaque jour", "semaine": "Chaque semaine", "mois": "Chaque mois"}
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
DEFAULT_BUDGET = 0.5
OPEN_STATUSES = ("brouillon", "simule", "approuve", "en_cours", "en_pause")


class RecurringError(ValueError):
    pass


@dataclass
class Recurrence:
    id: int
    workflow: str                  # code du workflow
    subject: str                   # « {date} » est remplacé par la date de l'échéance
    pole: str = ""
    cadence: str = "semaine"       # jour | semaine | mois
    weekday: int = 0               # 0 = lundi (cadence « semaine »)
    day: int = 1                   # 1 à 28 (cadence « mois »)
    hour: int = 8                  # heure locale
    prepare: bool = False          # faire préparer les brouillons par les agents
    budget: float = DEFAULT_BUDGET
    active: bool = True
    last_period: str = ""
    last_mission: int = 0
    last_status: str = ""
    created_by: str = ""
    created_at: str = field(default_factory=lambda: utcnow().date().isoformat())

    def when(self) -> str:
        at = f"à {self.hour} h"
        if self.cadence == "jour":
            return f"chaque jour {at}"
        if self.cadence == "semaine":
            return f"chaque {WEEKDAYS[self.weekday]} {at}"
        return f"le {self.day} de chaque mois {at}"


def period_key(rec: Recurrence, now: datetime) -> str:
    if rec.cadence == "jour":
        return now.date().isoformat()
    if rec.cadence == "semaine":
        year, week, _ = now.isocalendar()
        return f"{year}-S{week:02d}"
    return f"{now.year}-{now.month:02d}"


def slot_start(rec: Recurrence, now: datetime) -> datetime:
    """Moment où l'échéance de la période en cours est atteinte."""
    base = now.replace(minute=0, second=0, microsecond=0)
    if rec.cadence == "jour":
        return base.replace(hour=rec.hour)
    if rec.cadence == "semaine":
        monday = (now - timedelta(days=now.weekday())).replace(hour=rec.hour, minute=0,
                                                              second=0, microsecond=0)
        return monday + timedelta(days=rec.weekday)
    return base.replace(day=min(rec.day, 28), hour=rec.hour)


class RecurringService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, workflows,
                 autopilot, governor: Governor) -> None:
        self._sessions = sessions
        self.settings = settings
        self.workflows = workflows
        self.autopilot = autopilot
        self.governor = governor

    # ------------------------------------------------------------------ stockage
    def all(self) -> list[Recurrence]:
        raw = service_value(self._sessions, self.settings, KEY)
        try:
            items = json.loads(raw) if raw else []
        except ValueError:
            items = []
        return [Recurrence(**i) for i in items]

    def _save(self, items: list[Recurrence], by: str) -> None:
        set_service_value(self._sessions, self.settings, KEY,
                          json.dumps([asdict(i) for i in items]), by)

    def _get(self, items: list[Recurrence], rid: int) -> Recurrence:
        for i in items:
            if i.id == rid:
                return i
        raise RecurringError("Récurrence introuvable")

    # ------------------------------------------------------------------ gestion
    def add(self, workflow: str, subject: str, pole: str, cadence: str, weekday: int, day: int,
            hour: int, prepare: bool, budget: float, by: str) -> int:
        if workflow not in self.workflows.approved():
            raise RecurringError("Choisissez un workflow approuvé")
        subject = " ".join(subject.split())[:200]
        if not subject:
            raise RecurringError("Précisez le sujet de la mission")
        if cadence not in CADENCES:
            raise RecurringError("Cadence inconnue")
        if not 0 <= weekday <= 6 or not 1 <= day <= 28 or not 0 <= hour <= 23:
            raise RecurringError("Jour ou heure invalide")
        if budget < 0:
            raise RecurringError("Le budget ne peut pas être négatif")
        items = self.all()
        rid = max((i.id for i in items), default=0) + 1
        items.append(Recurrence(rid, workflow, subject, pole, cadence, weekday, day, hour,
                                prepare, budget, created_by=by))
        self._save(items, by)
        return rid

    def set_active(self, rid: int, active: bool, by: str) -> None:
        items = self.all()
        self._get(items, rid).active = active
        self._save(items, by)

    def remove(self, rid: int, by: str) -> None:
        items = self.all()
        self._get(items, rid)
        self._save([i for i in items if i.id != rid], by)

    # ------------------------------------------------------------------ exécution
    def run_due(self, now: datetime | None = None) -> dict:
        """Lance les échéances atteintes et pas encore traitées dans leur période."""
        now = (now or utcnow()).astimezone(ZoneInfo(self.settings.timezone))
        items = self.all()
        out = {"launched": 0, "skipped": 0}
        changed = False
        for rec in items:
            key = period_key(rec, now)
            if not rec.active or rec.last_period == key or now < slot_start(rec, now):
                continue
            result = self._launch(rec, key, now)
            changed = True
            if result == "lancee":
                out["launched"] += 1
            elif result == "ignoree":
                out["skipped"] += 1
        if changed:
            self._save(items, "récurrence")
        return out

    def run_now(self, rid: int, by: str, now: datetime | None = None) -> int:
        """Lancement manuel, hors période (n'empêche pas l'échéance suivante)."""
        items = self.all()
        rec = self._get(items, rid)
        now = (now or utcnow()).astimezone(ZoneInfo(self.settings.timezone))
        before = rec.last_period
        result = self._launch(rec, period_key(rec, now), now, manual=True)
        rec.last_period = before                 # un lancement manuel ne remplace pas l'échéance
        self._save(items, by)
        if result != "lancee":
            raise RecurringError(rec.last_status)
        return rec.last_mission

    def _launch(self, rec: Recurrence, key: str, now: datetime, manual: bool = False) -> str:
        """Retourne « lancee », « ignoree » (période consommée) ou « attente » (réessayer)."""
        if self.governor.is_stopped(ALL_CHANNELS) or self.governor.is_pole_stopped(rec.pole):
            rec.last_status = "Arrêt d'urgence en vigueur : en attente"
            return "attente"
        workflow = self.workflows.approved().get(rec.workflow)
        if workflow is None:
            rec.last_period, rec.last_status = key, "Workflow plus approuvé : échéance ignorée"
            self._journal(rec, rec.last_status, "blocked")
            return "ignoree"
        if rec.last_mission and not manual and self._still_open(rec.last_mission):
            rec.last_period = key
            rec.last_status = (f"Mission n° {rec.last_mission} pas terminée : échéance ignorée "
                               "pour ne pas empiler")
            self._journal(rec, rec.last_status, "blocked")
            return "ignoree"
        subject = rec.subject.replace("{date}", now.strftime("%d/%m/%Y"))
        mid = self.workflows.launch(workflow.id, subject, "récurrence", rec.pole)
        if rec.prepare:
            self.autopilot.set_budget(mid, rec.budget or None, "récurrence")
            self.autopilot.execute(mid, "récurrence")
        rec.last_period, rec.last_mission = key, mid
        rec.last_status = f"Mission n° {mid} lancée le {now.strftime('%d/%m/%Y à %H h')}"
        self._journal(rec, rec.last_status, "executed")
        return "lancee"

    def _still_open(self, mission_id: int) -> bool:
        with self._sessions() as s:
            m = s.get(Mission, mission_id)
            return bool(m and m.status in OPEN_STATUSES)

    def _journal(self, rec: Recurrence, text: str, status: str) -> None:
        with self._sessions() as s:
            s.add(JournalEntry(agent="chef", action_type="recurring.run", level=1,
                               channel="interne", pole=rec.pole, status=status,
                               summary=f"Travail récurrent « {rec.subject[:80]} » : {text}"[:480],
                               details={"recurrence": rec.id, "workflow": rec.workflow}))
            s.commit()
