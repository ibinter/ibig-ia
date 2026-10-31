"""Page « Workflows » (cahier des charges v2, 7.2) : créer, faire approuver et lancer un workflow.

Réservée à la direction et à l'administration : un workflow approuvé engage des missions.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import agent_control, procedures
from ..auth import Principal
from ..autopilot import AutopilotError
from ..runtime import Runtime
from ..workflows import WorkflowError, parse_text

STATUS_LABELS = {"brouillon": "Brouillon", "approuve": "Approuvé", "archive": "Archivé"}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    def act(who: Principal, fn, ok: str, to_mission: bool = False):
        direction_only(who)
        try:
            result = fn()
        except (WorkflowError, AutopilotError) as exc:
            return back("/workflows", f"Refusé : {exc}")
        return back(f"/missions/{result}" if to_mission else "/workflows", ok)

    @app.get("/workflows", response_class=HTMLResponse)
    def workflows_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        return page(request, "workflows.html", workflows=rt.workflows.all(),
                    status_labels=STATUS_LABELS, library=procedures.PROCEDURES,
                    poles=rt.org.poles, agents=agent_control.AGENTS,
                    agent_labels=agent_control.LABELS)

    @app.post("/workflows")
    def create(code: str = Form(""), titre: str = Form(""), declencheur: str = Form(""),
               pole: str = Form(""), etapes: str = Form(""), who: Principal = Depends(user)):
        return act(who, lambda: rt.workflows.create(code, titre, declencheur, pole,
                                                    parse_text(etapes), who.label),
                   "Brouillon créé : une autre personne doit l'approuver")

    @app.post("/workflows/adopter")
    def adopt(procedure: str = Form(""), who: Principal = Depends(user)):
        return act(who, lambda: rt.workflows.adopt(procedure, who.label),
                   "Procédure copiée en brouillon : à approuver avant de la lancer")

    @app.post("/workflows/{wid:int}/approuver")
    def approve(wid: int, who: Principal = Depends(user)):
        return act(who, lambda: rt.workflows.approve(wid, who.label), "Workflow approuvé")

    @app.post("/workflows/{wid:int}/archiver")
    def archive(wid: int, who: Principal = Depends(user)):
        return act(who, lambda: rt.workflows.archive(wid), "Workflow archivé")

    @app.post("/workflows/{wid:int}/lancer")
    def launch(wid: int, sujet: str = Form(""), pole: str = Form(""),
               who: Principal = Depends(user)):
        return act(who, lambda: rt.workflows.launch(wid, sujet, who.label, pole),
                   "Workflow lancé : suivez les tâches une à une", to_mission=True)
