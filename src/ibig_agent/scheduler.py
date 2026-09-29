"""Déclencheurs périodiques : relève des mails, rapport de 8 h, calendrier du lundi."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .runtime import Runtime

log = logging.getLogger(__name__)


def _safe(name: str, fn):
    def run():
        try:
            result = fn()
            log.info("%s : %s", name, result)
        except Exception:  # une tâche en échec ne doit pas arrêter les autres
            log.exception("Tâche %s en échec", name)

    return run


def next_monday(today: date | None = None, tz: str = "Africa/Abidjan") -> date:
    today = today or datetime.now(ZoneInfo(tz)).date()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def build_scheduler(rt: Runtime) -> BackgroundScheduler:
    tz = rt.settings.timezone
    sched = BackgroundScheduler(timezone=tz)
    sched.add_job(_safe("relève mails", rt.messagerie.poll), "interval",
                  minutes=rt.settings.mail_poll_minutes, id="mail_poll", max_instances=1,
                  coalesce=True)
    sched.add_job(_safe("rapport quotidien", rt.chef.run_daily), "cron",
                  hour=rt.settings.daily_report_hour, minute=0, id="daily_report")
    def alert_validators():
        rt.governor.flag_stale()
        return rt.notifier.run()

    sched.add_job(_safe("alertes valideurs", alert_validators), "interval",
                  minutes=15, id="validator_alerts", max_instances=1, coalesce=True)
    # Chaque lundi matin : calendrier éditorial de la semaine suivante, pour laisser
    # le temps aux valideurs de relire.
    sched.add_job(_safe("calendrier éditorial",
                        lambda: rt.communication.weekly_calendar(next_monday(tz=tz))),
                  "cron", day_of_week="mon", hour=7, minute=0, id="weekly_calendar")
    # Veille : alertes toutes les heures, indicateurs de la semaine chaque lundi à 8 h 15.
    sched.add_job(_safe("alertes de veille", rt.veille.check_alerts), "interval", hours=1,
                  id="watch_alerts", max_instances=1, coalesce=True)
    sched.add_job(_safe("indicateurs de la semaine", rt.veille.weekly_report), "cron",
                  day_of_week="mon", hour=8, minute=15, id="weekly_indicators")
    # Contenus web : 2 articles par mois et par site actif (1er et 15 du mois).
    sched.add_job(_safe("articles web", rt.contenus_web.run), "cron", day="1,15", hour=7,
                  minute=30, id="web_articles")
    return sched
