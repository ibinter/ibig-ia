"""Page « Anomalies » : sites et produits qui ne répondent pas, bugs signalés, incohérences.

Réservée à la direction et à l'administration. Elle ne fait que lire et classer : le service ne
modifie rien chez les produits (voir `anomalies.py`).
"""

from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..anomalies import CHECK_LABELS, SEVERITY_LABELS
from ..auth import Principal
from ..runtime import Runtime


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    running: set[str] = set()

    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/anomalies", response_class=HTMLResponse)
    def anomalies_page(request: Request, tous: int = 0, who: Principal = Depends(user)):
        direction_only(who)
        service = rt.anomalies
        items = service.findings()
        shown = items if tous else [f for f in items if f.status != "resolved"]
        return page(request, "anomalies.html", findings=shown, all_count=len(items),
                    show_all=bool(tous), severity_labels=SEVERITY_LABELS,
                    check_labels=CHECK_LABELS, targets=service.targets(),
                    extra_urls=service.extra_urls(), checking=bool(running))

    @app.post("/anomalies/verifier")
    def check_now(who: Principal = Depends(user)):
        direction_only(who)
        if running:
            return back("/anomalies", "Une vérification est déjà en cours : patientez")
        running.add("x")

        def work() -> None:
            try:
                rt.anomalies.scan_sites(deep=True)
                rt.anomalies.scan_bugs()
            finally:
                running.discard("x")

        threading.Thread(target=work, daemon=True).start()
        return back("/anomalies", "Vérification lancée : rechargez la page dans une minute")

    @app.post("/anomalies/statut")
    def set_status(cle: str = Form(...), statut: str = Form(...),
                   who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.anomalies.set_status(cle, statut, who.label)
        except ValueError as exc:
            return back("/anomalies", f"Refusé : {exc}")
        return back("/anomalies", {"ack": "Pris en compte", "resolved": "Marqué résolu",
                                   "open": "Rouvert"}.get(statut, "Mis à jour"))

    @app.post("/anomalies/adresses")
    def set_urls(adresses: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        n = rt.anomalies.set_extra_urls(adresses, who.label)
        return back("/anomalies", f"{n} adresse{'s' if n > 1 else ''} ajoutée{'s' if n > 1 else ''}"
                                  " à la surveillance")
