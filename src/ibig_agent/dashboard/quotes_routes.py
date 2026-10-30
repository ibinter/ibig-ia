"""Pages « Devis » et « Boîte unifiée » (cahier des charges, Lot 4).

Réservées à la direction et à l'administration : un devis engage l'entreprise vis-à-vis d'un client.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select

from ..auth import Principal
from ..catalog_store import list_offers
from ..db import ProcessedMessage, Prospect, Ticket
from ..quotes import STATUS_LABELS, QuoteError
from ..runtime import Runtime


def _int(raw: object, label: str) -> int:
    try:
        return int(str(raw).strip().replace(" ", ""))
    except ValueError as exc:
        raise QuoteError(f"{label} : un nombre entier est attendu") from exc


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    # ------------------------------------------------------------------ devis
    @app.get("/devis", response_class=HTMLResponse)
    def quotes(request: Request, statut: str = "", who: Principal = Depends(user)):
        direction_only(who)
        rows = rt.quotes.listing(statut if statut in STATUS_LABELS else "")
        with rt.sessions() as s:
            names = {p.id: p.name or p.email for p in s.scalars(select(Prospect).where(
                Prospect.id.in_({q.prospect_id for q in rows}))).all()} if rows else {}
        return page(request, "devis.html", rows=rows, names=names, statut=statut,
                    labels=STATUS_LABELS, pipe=rt.quotes.pipeline())

    @app.post("/prospects/{pid:int}/devis")
    def new_quote(pid: int, notes: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            qid = rt.quotes.create(pid, who.label, notes)
        except QuoteError as exc:
            return back(f"/prospects/{pid}", f"Refusé : {exc}")
        return back(f"/devis/{qid}", "Brouillon créé : ajoutez les lignes")

    @app.get("/devis/{qid:int}", response_class=HTMLResponse)
    def quote(request: Request, qid: int, q: str = "", who: Principal = Depends(user)):
        direction_only(who)
        found = rt.quotes.get(qid)
        if found is None:
            raise HTTPException(404)
        devis, lines = found
        with rt.sessions() as s:
            prospect = s.get(Prospect, devis.prospect_id)
        offers = list_offers(rt.sessions, q=q, limit=15)[0] if q.strip() else []
        return page(request, "devis_detail.html", d=devis, lines=lines, prospect=prospect,
                    offers=offers, q=q, labels=STATUS_LABELS)

    @app.post("/devis/{qid:int}/ligne")
    async def add_line(request: Request, qid: int, who: Principal = Depends(user)):
        direction_only(who)
        form = await request.form()
        try:
            price_raw = str(form.get("prix", "")).strip()
            rt.quotes.add_line(
                qid, who.label, offer_code=str(form.get("code", "")).strip(),
                label=str(form.get("libelle", "")).strip(),
                price=_int(price_raw, "Prix") if price_raw else None,
                qty=_int(form.get("quantite") or "1", "Quantité"))
        except QuoteError as exc:
            return back(f"/devis/{qid}", f"Refusé : {exc}")
        return back(f"/devis/{qid}", "Ligne ajoutée")

    @app.post("/devis/{qid:int}/ligne/{lid:int}/retirer")
    def remove_line(qid: int, lid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.quotes.remove_line(qid, lid, who.label)
        except QuoteError as exc:
            return back(f"/devis/{qid}", f"Refusé : {exc}")
        return back(f"/devis/{qid}", "Ligne retirée")

    @app.get("/devis/{qid:int}/imprimer", response_class=HTMLResponse)
    def print_quote(request: Request, qid: int, who: Principal = Depends(user)):
        direction_only(who)
        found = rt.quotes.get(qid)
        if found is None:
            raise HTTPException(404)
        devis, lines = found
        if devis.status == "brouillon":
            return back(f"/devis/{qid}", "Refusé : validez le devis avant de l'imprimer")
        with rt.sessions() as s:
            prospect = s.get(Prospect, devis.prospect_id)
        return page(request, "devis_imprimer.html", d=devis, lines=lines, prospect=prospect)

    @app.post("/devis/{qid:int}/{action}")
    def quote_action(qid: int, action: str, jours: str = Form("30"),
                     who: Principal = Depends(user)):
        direction_only(who)
        svc = rt.quotes
        steps = {
            "valider": (lambda: svc.validate(qid, who.label, _int(jours, "Validité (jours)")),
                        "Devis validé"),
            "envoyer": (lambda: svc.mark_sent(qid, who.label), "Devis marqué comme envoyé"),
            "accepter": (lambda: svc.decide(qid, True, who.label),
                         "Devis accepté : prospect gagné"),
            "refuser": (lambda: svc.decide(qid, False, who.label),
                        "Devis refusé : prospect perdu"),
            "annuler": (lambda: svc.cancel(qid, who.label), "Devis annulé"),
        }
        if action not in steps:
            raise HTTPException(404)
        fn, ok = steps[action]
        try:
            fn()
        except QuoteError as exc:
            return back(f"/devis/{qid}", f"Refusé : {exc}")
        return back(f"/devis/{qid}", ok)

    # ------------------------------------------------------------------ boîte unifiée
    @app.get("/boite", response_class=HTMLResponse)
    def inbox(request: Request, boite: str = "", pole: str = "", decision: str = "",
              q: str = "", who: Principal = Depends(user)):
        direction_only(who)
        with rt.sessions() as s:
            qry = select(ProcessedMessage)
            if boite:
                qry = qry.where(ProcessedMessage.mailbox == boite)
            if pole:
                qry = qry.where(ProcessedMessage.pole == pole)
            if decision:
                qry = qry.where(ProcessedMessage.decision == decision)
            if q.strip():
                like = f"%{q.strip()}%"
                qry = qry.where(ProcessedMessage.subject.ilike(like)
                                | ProcessedMessage.sender.ilike(like))
            rows = s.scalars(qry.order_by(desc(ProcessedMessage.processed_at)).limit(150)).all()
            boxes = [r[0] for r in s.execute(select(ProcessedMessage.mailbox).distinct()).all()]
            decisions = [r[0] for r in s.execute(
                select(ProcessedMessage.decision).distinct()).all() if r[0]]
            open_tickets = s.scalar(select(func.count()).select_from(Ticket).where(
                Ticket.status == "ouvert")) or 0
        return page(request, "boite.html", rows=rows, boxes=sorted(boxes),
                    decisions=sorted(decisions), poles=rt.org.poles, boite=boite, pole=pole,
                    decision=decision, q=q, open_tickets=open_tickets)
