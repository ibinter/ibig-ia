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
    sched.add_job(_safe("validations en retard", rt.governor.flag_stale), "interval",
                  hours=1, id="stale_approvals")
    # Chaque lundi matin : calendrier éditorial de la semaine suivante, pour laisser
    # le temps aux valideurs de relire.
    sched.add_job(_safe("calendrier éditorial",
                        lambda: rt.communication.weekly_calendar(next_monday(tz=tz))),
                  "cron", day_of_week="mon", hour=7, minute=0, id="weekly_calendar")
    return sched
