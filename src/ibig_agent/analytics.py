"""Audience centralisée : visites et actions des visiteurs de tous les sites du groupe.

Respect de la vie privée : aucun cookie, aucune adresse IP conservée. Le visiteur est une empreinte
anonyme (IP + navigateur + jour + secret) qui change chaque jour et ne permet pas de remonter à la personne.
Seuls les sites connus de l'écosystème sont acceptés ; les robots et les sondes sont ignorés.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, distinct, func, select

from .db import Visit, utcnow

EVENTS = {"pageview", "click", "form", "custom"}
BOT = re.compile(r"bot|crawl|spider|slurp|headless|monitor|uptime|ibig-seo|python|curl|wget|preview|lighthouse",
                 re.IGNORECASE)
RETENTION_DAYS = 400
MAX_PER_MINUTE = 90


def host_of(value: str) -> str:
    h = urlsplit(value if "//" in value else "//" + value).hostname or ""
    return h.lower().removeprefix("www.")


def device_of(ua: str) -> str:
    u = ua.lower()
    if "ipad" in u or "tablet" in u:
        return "tablette"
    return "mobile" if ("mobi" in u or "android" in u or "iphone" in u) else "ordi"


class AnalyticsService:
    def __init__(self, sessions, secret: str, allowed_sites: Callable[[], set[str]]):
        self._sessions = sessions
        self._secret = secret
        self._allowed = allowed_sites
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._writes = 0

    def _fingerprint(self, ip: str, ua: str) -> str:
        day = utcnow().strftime("%Y-%m-%d")
        return hashlib.sha256(f"{self._secret}|{day}|{ip}|{ua}".encode()).hexdigest()[:16]

    def _limited(self, who: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._hits[who] if now - t < 60]
            recent.append(now)
            self._hits[who] = recent
            if len(self._hits) > 5000:
                self._hits = defaultdict(list, {k: v for k, v in self._hits.items() if v and now - v[-1] < 60})
            return len(recent) > MAX_PER_MINUTE

    def record(self, site: str, path: str, event: str, label: str, ref: str, ua: str, ip: str) -> bool:
        """Enregistre un événement ; renvoie False s'il est ignoré (site inconnu, robot, abus)."""
        site = host_of(site)
        if not site or site not in {host_of(a) for a in self._allowed()}:
            return False
        if not ua or BOT.search(ua):
            return False
        visitor = self._fingerprint(ip, ua)
        if self._limited(visitor):
            return False
        path = (urlsplit(path).path or "/")[:300]
        ref_host = host_of(ref) if ref else ""
        ref_host = "" if ref_host == site else ref_host
        row = Visit(site=site, path=path, event=event if event in EVENTS else "custom",
                    label=label.strip()[:120], ref=ref_host[:120], visitor=visitor, device=device_of(ua))
        with self._sessions() as s:
            s.add(row)
            self._writes += 1
            if self._writes % 500 == 0:
                s.execute(delete(Visit).where(Visit.at < utcnow() - timedelta(days=RETENTION_DAYS)))
            s.commit()
        return True

    # ---------------------------------------------------------------- lecture

    def overview(self, days: int = 7) -> list[dict]:
        """Une ligne par site : visiteurs, pages vues, actions, sur la période."""
        since = utcnow() - timedelta(days=days)
        with self._sessions() as s:
            totals = s.execute(select(Visit.site, func.count(distinct(Visit.visitor)), func.count(Visit.id))
                               .where(Visit.at >= since).group_by(Visit.site)).all()
            views = dict(s.execute(select(Visit.site, func.count(Visit.id))
                                   .where(Visit.at >= since, Visit.event == "pageview")
                                   .group_by(Visit.site)).all())
        out = [{"site": site, "visitors": int(v), "views": int(views.get(site, 0)),
                "actions": int(total) - int(views.get(site, 0))} for site, v, total in totals]
        return sorted(out, key=lambda r: (-r["views"], r["site"]))

    def detail(self, site: str, days: int = 7) -> dict:
        since = utcnow() - timedelta(days=days)
        with self._sessions() as s:
            base = [Visit.site == site, Visit.at >= since]

            def top(col, *extra, limit=8):
                return [(k or "direct", int(n)) for k, n in s.execute(
                    select(col, func.count(Visit.id)).where(*base, *extra).group_by(col)
                    .order_by(func.count(Visit.id).desc()).limit(limit)).all()]

            pages = top(Visit.path, Visit.event == "pageview", limit=10)
            refs = top(Visit.ref, Visit.event == "pageview")
            devices = top(Visit.device, Visit.event == "pageview", limit=3)
            actions = [(f"{e} · {lab}" if lab else e, int(n)) for e, lab, n in s.execute(
                select(Visit.event, Visit.label, func.count(Visit.id))
                .where(*base, Visit.event != "pageview").group_by(Visit.event, Visit.label)
                .order_by(func.count(Visit.id).desc()).limit(10)).all()]
            stamps = s.execute(select(Visit.at, Visit.visitor, Visit.event).where(*base)).all()
        daily: dict[str, dict] = {}
        for at, visitor, event in stamps:
            d = daily.setdefault(at.strftime("%Y-%m-%d"), {"views": 0, "visitors": set()})
            d["visitors"].add(visitor)
            d["views"] += event == "pageview"
        series = [{"day": k, "views": v["views"], "visitors": len(v["visitors"])} for k, v in sorted(daily.items())]
        return {"pages": pages, "refs": refs, "devices": devices, "actions": actions, "series": series}
