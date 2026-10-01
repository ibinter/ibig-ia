"""Déclencheurs périodiques : relève des mails, rapport de 8 h, calendrier du lundi.

Chaque exécution est enregistrée (table job_status) : le tableau de bord affiche la santé
des tâches, et la direction est alertée après ALERT_AFTER échecs de suite.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .db import JobStatus, JournalEntry, utcnow
from .runtime import Runtime

log = logging.getLogger(__name__)
ALERT_AFTER = 3


@dataclass
class JobSpec:
    id: str
    name: str
    frequency: str             # en clair, pour le tableau de bord
    fn: Callable[[], object]
    trigger: str
    options: dict = field(default_factory=dict)
    manual: bool = True        # « Lancer maintenant » proposé au tableau de bord


def next_monday(today: date | None = None, tz: str = "Africa/Abidjan") -> date:
    today = today or datetime.now(ZoneInfo(tz)).date()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def this_monday(tz: str = "Africa/Abidjan") -> date:
    today = datetime.now(ZoneInfo(tz)).date()
    return today - timedelta(days=today.weekday())


def job_specs(rt: Runtime) -> list[JobSpec]:
    """Toutes les tâches planifiées, dans l'ordre d'affichage."""
    from .privacy import purge

    s = rt.settings
    tz = s.timezone

    def alert_validators():
        rt.governor.flag_stale()
        return rt.notifier.run(), rt.wa_validation.notify()

    every = {"max_instances": 1, "coalesce": True}
    return [
        JobSpec("mail_poll", "Relève des mails", f"toutes les {s.mail_poll_minutes} min",
                lambda: rt.messagerie.poll(), "interval",
                {"minutes": s.mail_poll_minutes, **every}),
        JobSpec("scheduled_posts", "Publications programmées", "toutes les 5 min",
                lambda: rt.publish_due(), "interval", {"minutes": 5, **every}),
        JobSpec("validator_alerts", "Alertes aux valideurs", "toutes les 15 min",
                alert_validators, "interval", {"minutes": 15, **every}),
        JobSpec("prospect_qualification", "Qualification des prospects", "toutes les 15 min",
                lambda: rt.commercial.qualify_new(), "interval", {"minutes": 15, **every}),
        JobSpec("watch_alerts", "Alertes de veille", "toutes les heures",
                lambda: rt.veille.check_alerts(), "interval", {"hours": 1, **every}),
        JobSpec("comment_watch", "Veille des commentaires", "toutes les heures",
                lambda: rt.comments.run(), "interval", {"hours": 1, **every}),
        # Recherche par le sens : réindexation des passages modifiés (et au démarrage)
        JobSpec("kb_index", "Index de la base", "toutes les heures",
                lambda: rt.index_knowledge(), "interval",
                {"hours": 1, **every,
                 "next_run_time": datetime.now(ZoneInfo(tz)) + timedelta(seconds=30)}),
        JobSpec("daily_report", "Rapport quotidien", f"chaque jour à {s.daily_report_hour} h",
                lambda: rt.chef.run_daily(), "cron",
                {"hour": s.daily_report_hour, "minute": 0}),
        # Commercial : relances, essais et démonstrations en semaine, aux heures ouvrées
        JobSpec("sales_followup", "Suivi commercial",
                f"en semaine, de {s.business_open_hour} h à {s.business_close_hour} h",
                lambda: rt.commercial.run(), "cron",
                {"day_of_week": "mon-fri", "minute": 10, **every,
                 "hour": f"{s.business_open_hour}-{s.business_close_hour - 1}"}),
        # Chaque lundi : calendrier de la semaine suivante (temps de relecture), plan, bilan
        JobSpec("weekly_calendar", "Calendrier éditorial", "le lundi à 7 h",
                lambda: rt.communication.weekly_calendar(next_monday(tz=tz)), "cron",
                {"day_of_week": "mon", "hour": 7, "minute": 0}),
        JobSpec("weekly_plan", "Plan de la semaine", "le lundi à 7 h 30",
                lambda: rt.planner.run(this_monday(tz)), "cron",
                {"day_of_week": "mon", "hour": 7, "minute": 30}),
        JobSpec("weekly_indicators", "Indicateurs de la semaine", "le lundi à 8 h 15",
                lambda: rt.veille.weekly_report(), "cron",
                {"day_of_week": "mon", "hour": 8, "minute": 15}),
        # Contenus web : 2 articles par mois et par site actif
        JobSpec("web_articles", "Articles web", "le 1er et le 15 du mois",
                lambda: rt.contenus_web.run(), "cron", {"day": "1,15", "hour": 7, "minute": 30}),
        JobSpec("monthly_review", "Revue mensuelle", "le 1er du mois à 9 h",
                lambda: rt.revue.run(), "cron", {"day": 1, "hour": 9, "minute": 0}),
        # Conservation limitée des données (section 13)
        JobSpec("data_retention", "Purge des données anciennes", "le 1er du mois à 3 h",
                lambda: purge(rt.sessions, s.retention_months), "cron",
                {"day": 1, "hour": 3, "minute": 0}, manual=False),
    ]


def _safe(name: str, fn):
    """Lancement ponctuel (bouton du tableau de bord), sans suivi : erreurs journalisées."""
    def run():
        try:
            log.info("%s : %s", name, fn())
        except Exception:  # une tâche en échec ne doit pas arrêter les autres
            log.exception("Tâche %s en échec", name)

    return run


def _summary(result: object) -> str:
    if isinstance(result, dict):
        text = " · ".join(f"{str(k).replace('_', ' ')} : {v}" for k, v in result.items())
    elif hasattr(result, "__dict__") and not isinstance(result, type):
        text = " · ".join(f"{k.replace('_', ' ')} : {v}" for k, v in vars(result).items()
                          if not k.startswith("_") and isinstance(v, int | float | str))
    else:
        text = "" if result is None else str(result)
    return text if len(text) <= 300 else text[:297] + "…"


def tracked(rt: Runtime, spec_id: str, name: str, fn: Callable[[], object]):
    """Exécute fn en enregistrant début, fin, durée, résultat ou erreur ; une tâche en
    échec ne doit jamais arrêter les autres, ni le suivi faire échouer la tâche."""

    def save(**values) -> JobStatus | None:
        try:
            with rt.sessions() as s:
                row = s.get(JobStatus, spec_id) or JobStatus(job_id=spec_id, runs=0,
                                                             failures=0, total_failures=0)
                row.name = name
                for k, v in values.items():
                    setattr(row, k, v(row) if callable(v) else v)
                s.merge(row)
                s.commit()
                return row
        except Exception:  # le suivi ne bloque jamais la tâche
            log.exception("Suivi de la tâche %s impossible", spec_id)
            return None

    def run():
        start, t0 = utcnow(), time.monotonic()
        save(last_start=start, running=True)
        try:
            result = fn()
        except Exception as exc:  # erreur enregistrée et affichée
            log.exception("Tâche %s en échec", name)
            err = f"{type(exc).__name__} : {exc}"[:2000]
            row = save(last_end=utcnow(), running=False, last_error_at=utcnow(), last_error=err,
                       duration_ms=int((time.monotonic() - t0) * 1000),
                       runs=lambda r: (r.runs or 0) + 1,
                       failures=lambda r: (r.failures or 0) + 1,
                       total_failures=lambda r: (r.total_failures or 0) + 1)
            if row is not None and row.failures >= ALERT_AFTER and not row.alerted:
                _alert(rt, spec_id, name, row.failures, err)
                save(alerted=True)
            return None
        log.info("%s : %s", name, result)
        save(last_end=utcnow(), last_ok=utcnow(), running=False, last_result=_summary(result),
             duration_ms=int((time.monotonic() - t0) * 1000),
             runs=lambda r: (r.runs or 0) + 1, failures=0, alerted=False)
        return result

    return run


def _alert(rt: Runtime, spec_id: str, name: str, failures: int, err: str) -> None:
    subject = f"[IBIG] Tâche « {name} » en échec {failures} fois de suite"
    body = (f"La tâche automatique « {name} » échoue depuis {failures} exécutions.\n\n"
            f"Dernière erreur : {err}\n\nDétail et relance : page « Santé des tâches ».")
    try:
        with rt.sessions() as s:
            s.add(JournalEntry(agent="systeme", action_type="system.job_failed", level=1,
                               channel="systeme", status="flagged", summary=subject,
                               details={"tache": spec_id, "erreur": err}))
            s.commit()
        rt.veille.notify_direction(subject, body)
    except Exception:  # l'alerte ne doit pas casser la boucle
        log.exception("Alerte de la tâche %s impossible", spec_id)


def build_scheduler(rt: Runtime) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=rt.settings.timezone)
    for spec in job_specs(rt):
        sched.add_job(tracked(rt, spec.id, spec.name, spec.fn), spec.trigger, id=spec.id,
                      **spec.options)
    rt.scheduler = sched
    return sched
