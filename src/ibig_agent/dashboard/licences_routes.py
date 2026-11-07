"""Licences et abonnements des clients : liste, ajout, renouvellement, suspension (direction uniquement)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..licences import PLANS
from ..runtime import Runtime

STATE_LABEL = {"active": "Active", "bientot": "Expire bientôt", "expiree": "Expirée", "suspendue": "Suspendue",
               "resiliee": "Résiliée", "sans_echeance": "Sans échéance"}
STATE_TONE = {"active": "green", "sans_echeance": "green", "bientot": "amber", "expiree": "red",
              "suspendue": "amber", "resiliee": "red"}


def parse_date(value: str) -> date | None:
    value = value.strip()
    return date.fromisoformat(value) if value else None


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/licences", response_class=HTMLResponse)
    def licences_page(request: Request, filtre: str = "", who: Principal = Depends(user)):
        direction_only(who)
        rows = rt.licences.rows()
        if filtre in STATE_LABEL:
            rows = [r for r in rows if r["state"] == filtre]
        products = sorted({a.name for a in rt.ecosystem.assets()})
        return page(request, "licences.html", rows=rows, summary=rt.licences.summary(), plans=list(PLANS),
                    labels=STATE_LABEL, tones=STATE_TONE, filtre=filtre, products=products,
                    today=datetime.now(UTC).date().isoformat())

    @app.post("/licences")
    def licence_add(client: str = Form(""), product: str = Form(""), plan: str = Form("annuel"),
                    amount: str = Form("0"), starts_on: str = Form(""), ends_on: str = Form(""),
                    contact: str = Form(""), site: str = Form(""), notes: str = Form(""),
                    who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.licences.add(client, product, plan, int(amount or 0), parse_date(starts_on) or datetime.now(UTC).date(),
                            parse_date(ends_on), contact, site, notes)
        except ValueError as exc:
            return back("/licences", f"Non enregistrée : {exc}")
        return back("/licences", "Licence enregistrée")

    @app.post("/licences/synchroniser")
    def licence_sync(who: Principal = Depends(user)):
        direction_only(who)
        try:
            res = rt.licences.sync_file(rt.settings.state_dir / "licences-collectees.json")
        except ValueError as exc:
            return back("/licences", str(exc))
        created = sum(r["created"] for r in res)
        updated = sum(r["updated"] for r in res)
        return back("/licences", f"Synchronisé : {created} nouvelle(s), {updated} mise(s) à jour")

    @app.post("/licences/{lid}/renouveler")
    def licence_renew(lid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            end = rt.licences.renew(lid)
        except ValueError as exc:
            return back("/licences", str(exc))
        return back("/licences", f"Renouvelée jusqu'au {end.strftime('%d/%m/%Y')}")

    @app.post("/licences/{lid}/statut")
    def licence_status(lid: int, statut: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.licences.set_status(lid, statut)
        except ValueError as exc:
            return back("/licences", str(exc))
        return back("/licences", "Statut mis à jour")
