"""Page « Finance » (cahier des charges, Lot 5) : paiements à confirmer, chiffre d'affaires
confirmé, commissions et taux par pôle. Réservée à la direction et à l'administration."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..finance import COMMISSION_LABELS, METHODS, PAYMENT_LABELS, FinanceError
from ..runtime import Runtime


def _number(raw: object, label: str) -> float:
    try:
        return float(str(raw).strip().replace(" ", "").replace(",", "."))
    except ValueError as exc:
        raise FinanceError(f"{label} : un nombre est attendu") from exc


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/finance", response_class=HTMLResponse)
    def finance(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        svc = rt.finance
        return page(request, "finance.html", s=svc.summary(),
                    pending=svc.payments("a_confirmer"), recent=svc.payments("confirme")[:30],
                    commissions=svc.commissions(), rates=svc.rates(), poles=rt.org.poles,
                    methods=METHODS, pay_labels=PAYMENT_LABELS, com_labels=COMMISSION_LABELS)

    @app.post("/finance/devis/{qid:int}/paiement")
    def record(qid: int, montant: str = Form(""), mode: str = Form(""),
               reference: str = Form(""), preuve: str = Form(""),
               who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.finance.record(qid, int(_number(montant, "Montant")), mode, reference, preuve,
                              who.label)
        except FinanceError as exc:
            return back(f"/devis/{qid}", f"Refusé : {exc}")
        return back("/finance", "Paiement saisi : une autre personne doit le confirmer")

    @app.post("/finance/paiement/{pid:int}/{action}")
    def payment_action(pid: int, action: str, motif: str = Form(""),
                       who: Principal = Depends(user)):
        direction_only(who)
        if action not in ("confirmer", "rejeter"):
            raise HTTPException(404)
        try:
            if action == "confirmer":
                note = rt.finance.confirm(pid, who.label)
                return back("/finance", f"Paiement confirmé. {note}")
            rt.finance.reject(pid, motif, who.label)
        except FinanceError as exc:
            return back("/finance", f"Refusé : {exc}")
        return back("/finance", "Paiement rejeté")

    @app.post("/finance/commission/{cid:int}/{action}")
    def commission_action(cid: int, action: str, who: Principal = Depends(user)):
        direction_only(who)
        if action not in ("valider", "verser", "annuler"):
            raise HTTPException(404)
        try:
            rt.finance.commission_step(cid, action, who.label)
        except FinanceError as exc:
            return back("/finance", f"Refusé : {exc}")
        return back("/finance", "Commission mise à jour")

    @app.post("/finance/taux")
    def rate(pole: str = Form(""), taux: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.finance.set_rate(pole, _number(taux, "Taux"), who.label)
        except FinanceError as exc:
            return back("/finance", f"Refusé : {exc}")
        return back("/finance", f"Taux du pôle {pole} enregistré")
