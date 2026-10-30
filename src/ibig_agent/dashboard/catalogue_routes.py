"""Page « Catalogue » : registre des offres (statut, version, tarif, validation).

La Direction y décide du statut d'une offre et fixe le tarif d'une offre ambiguë ; les fichiers
lus par l'agent sont régénérés aussitôt depuis le registre (aucun appel réseau).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..catalog_import import regenerate
from ..catalog_store import LABELS, STATUSES, list_offers, set_price, set_status, status_counts
from ..configstore import reload_knowledge
from ..runtime import Runtime


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    def apply(message: str) -> str:
        """Après une décision : fichiers de l'agent régénérés, base de connaissances rechargée."""
        stats = regenerate(rt.settings.knowledge_dir, rt.sessions)
        if stats.get("ecrits") or stats.get("retires"):
            reload_knowledge(rt)
        return message

    @app.get("/catalogue", response_class=HTMLResponse)
    def catalogue(request: Request, q: str = "", pole: str = "", statut: str = "",
                  who: Principal = Depends(user)):
        rows, total = list_offers(rt.sessions, q=q, pole=pole,
                                  status=statut if statut in STATUSES else "")
        if who.role == "valideur":
            rows = [o for o in rows if o.pole in who.poles]
        return page(request, "catalogue.html", rows=rows, total=total, q=q, pole=pole,
                    statut=statut, statuses=STATUSES, labels=LABELS,
                    counts=status_counts(rt.sessions), poles=rt.org.poles,
                    can_edit=who.role != "valideur")

    @app.post("/catalogue/{offer_id:int}/statut")
    def change_status(offer_id: int, statut: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        ok, msg = set_status(rt.sessions, offer_id, statut, who.label)
        return back("/catalogue", apply(msg) if ok else f"Refusé : {msg}")

    @app.post("/catalogue/{offer_id:int}/tarif")
    def change_price(offer_id: int, prix: int = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        ok, msg = set_price(rt.sessions, offer_id, prix, who.label)
        return back("/catalogue", apply(msg) if ok else f"Refusé : {msg}")
