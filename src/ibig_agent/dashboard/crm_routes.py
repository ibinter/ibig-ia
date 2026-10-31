"""Fiche prospect (CRM, cahier des charges section 7) : étape, origine, affectation, notes, fil
d'activités. Les valideurs ne voient et ne traitent que les prospects de leurs pôles."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select

from ..agents.commercial import RESERVED_STAGES, STAGE_LABELS, STAGES, VERIFICATIONS
from ..auth import Principal
from ..db import JournalEntry, Prospect
from ..runtime import Runtime

VERIFICATION_LABELS = {"non_verifie": "Non vérifiée", "verifie": "Vérifiée", "invalide": "Invalide"}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def load(pid: int, who: Principal) -> Prospect:
        with rt.sessions() as s:
            p = s.get(Prospect, pid)
        if p is None or (who.role == "valideur" and p.pole not in who.poles):
            raise HTTPException(404)
        return p

    def reasons(pid: int) -> list[str]:
        """Pourquoi ce score ? Les raisons données par l'agent à la dernière qualification."""
        with rt.sessions() as s:
            rows = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "commercial.qualify").order_by(
                desc(JournalEntry.id)).limit(500)).all()
        for r in rows:
            if (r.details or {}).get("prospect_id") == pid:
                return [str(x) for x in (r.details or {}).get("raisons") or []]
        return []

    @app.get("/prospects/{pid:int}", response_class=HTMLResponse)
    def fiche(request: Request, pid: int, who: Principal = Depends(user)):
        p = load(pid, who)
        missing = [label for label, value in (
            ("nom du contact", p.name), ("pôle", p.pole), ("besoin documenté", p.need),
            ("origine (adresse de la source)", p.source_url),
            ("solution visée", p.solution)) if not value]
        return page(request, "prospect.html", p=p, activities=rt.commercial.activities(pid),
                    reasons=reasons(pid), missing=missing, stages=STAGES, labels=STAGE_LABELS,
                    reserved=RESERVED_STAGES, verifications=VERIFICATIONS,
                    verification_labels=VERIFICATION_LABELS, can_assign=who.role != "valideur")

    def act(pid: int, who: Principal, fn, ok: str):
        load(pid, who)
        try:
            fn()
        except ValueError as exc:
            return back(f"/prospects/{pid}", f"Refusé : {exc}")
        return back(f"/prospects/{pid}", ok)

    @app.post("/prospects/{pid:int}/etape")
    def stage(pid: int, etape: str = Form(...), note: str = Form(""),
              who: Principal = Depends(user)):
        return act(pid, who, lambda: rt.commercial.set_stage(pid, etape, who.label, note),
                   "Étape enregistrée")

    @app.post("/prospects/{pid:int}/rendez-vous")
    def appointment(pid: int, who: Principal = Depends(user)):
        return act(pid, who, lambda: rt.commercial.propose_appointment(pid, who.label),
                   "Proposition de rendez-vous préparée : elle attend votre validation")

    @app.post("/prospects/{pid:int}/apres-vente")
    def after_sales(pid: int, who: Principal = Depends(user)):
        return act(pid, who, lambda: rt.commercial.after_sales(pid, who.label),
                   "Message d'après-vente préparé : il attend votre validation")

    @app.post("/prospects/{pid:int}/note")
    def note_(pid: int, texte: str = Form(...), who: Principal = Depends(user)):
        return act(pid, who, lambda: rt.commercial.add_note(pid, texte, who.label),
                   "Note ajoutée")

    @app.post("/prospects/{pid:int}/origine")
    def origin(pid: int, source_url: str = Form(""), verification: str = Form("non_verifie"),
               who: Principal = Depends(user)):
        return act(pid, who, lambda: rt.commercial.set_origin(pid, source_url, verification,
                                                              who.label), "Origine enregistrée")

    @app.post("/prospects/{pid:int}/affectation")
    def assign(pid: int, agent: str = Form(""), commercial: str = Form(""),
               who: Principal = Depends(user)):
        if who.role == "valideur":
            raise HTTPException(403)
        return act(pid, who, lambda: rt.commercial.assign(pid, agent, commercial, who.label),
                   "Affectation enregistrée")
