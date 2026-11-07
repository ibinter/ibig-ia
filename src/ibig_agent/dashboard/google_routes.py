"""Page « Positions sur Google » (direction) : clics, affichages, position moyenne de chaque site."""

from __future__ import annotations

import logging
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..runtime import Runtime

log = logging.getLogger(__name__)


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    running = threading.Event()

    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/google", response_class=HTMLResponse)
    def google_page(request: Request, site: str = "", who: Principal = Depends(user)):
        direction_only(who)
        rows = rt.google.overview()
        known = {r["domain"] for r in rows}
        selected = site if site in known else next((r["domain"] for r in rows if r["data"]), "")
        return page(request, "google.html", rows=rows, selected=selected, detail=rt.google.detail(selected),
                    configured=rt.google.configured(), syncing=running.is_set(),
                    total_clicks=sum(r["data"]["clicks"] for r in rows if r["data"]),
                    total_impressions=sum(r["data"]["impressions"] for r in rows if r["data"]))

    @app.post("/google/actualiser")
    def google_refresh(who: Principal = Depends(user)):
        direction_only(who)
        if running.is_set():
            return back("/google", "Une actualisation est déjà en cours")
        if not rt.google.configured():
            return back("/google", "La clé Google n'est pas configurée")

        def work():
            try:
                rt.google.sync()
            except Exception:
                log.exception("Actualisation Google Search Console échouée")
            finally:
                running.clear()

        running.set()
        threading.Thread(target=work, daemon=True).start()
        return back("/google", "Actualisation lancée : revenez dans une minute")
