"""Console de maintenance (direction uniquement) : demander, confirmer, annuler une action de la liste fermée."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..maintenance_catalog import CATALOG
from ..runtime import Runtime

STATUS_LABEL = {"demandee": "À confirmer", "confirmee": "En attente d'exécution", "en_cours": "En cours",
                "ok": "Terminée", "erreur": "Échec", "annulee": "Annulée", "refusee": "Refusée"}
STATUS_TONE = {"demandee": "amber", "confirmee": "amber", "en_cours": "amber", "ok": "green",
               "erreur": "red", "annulee": "red", "refusee": "red"}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role not in ("admin", "direction"):
            raise HTTPException(403)

    @app.get("/maintenance", response_class=HTMLResponse)
    def maintenance_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        label = {a: e["label"] for a, e in CATALOG.items()}
        target = {(a, k): t["label"] for a, e in CATALOG.items() for k, t in e["targets"].items()}
        return page(request, "maintenance.html", catalog=rt.maintenance.catalog(), rows=rt.maintenance.recent(),
                    labels=STATUS_LABEL, tones=STATUS_TONE, action_label=label, target_label=target)

    @app.post("/maintenance/demander")
    def maintenance_request(action: str = Form(""), target: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.maintenance.request(action, target, who.email)
        except ValueError as exc:
            return back("/maintenance", str(exc))
        return back("/maintenance", "Demande créée : confirmez-la pour qu'elle soit exécutée")

    @app.post("/maintenance/{mid}/confirmer")
    def maintenance_confirm(mid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.maintenance.confirm(mid, who.email)
        except ValueError as exc:
            return back("/maintenance", str(exc))
        return back("/maintenance", "Confirmée : le serveur l'exécute dans la minute")

    @app.post("/maintenance/{mid}/annuler")
    def maintenance_cancel(mid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.maintenance.cancel(mid, who.email)
        except ValueError as exc:
            return back("/maintenance", str(exc))
        return back("/maintenance", "Demande annulée")
