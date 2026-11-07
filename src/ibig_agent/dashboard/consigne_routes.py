"""Page « Consigne » : la direction écrit une demande libre, l'agent concerné s'en charge.

Réservée à la direction et à l'administration. Les agents préparent des brouillons : rien n'est envoyé,
publié, payé ni modifié sur un système sans validation humaine.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import agent_control
from ..auth import Principal
from ..consigne import STATUS_LABELS, ConsigneError

STATUS_TONE = {"en_cours": "blue", "prise": "green", "a_preciser": "amber", "repondue": "green",
               "redirigee": "blue", "echec": "red"}
EXAMPLES = ("Vérifie que Lokativo fonctionne", "Relance les prospects de la semaine dernière",
            "Prépare un message pour les clients Scolaby", "Fais le point sur les sauvegardes")


def register(app: FastAPI, rt, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/consigne", response_class=HTMLResponse)
    def consigne_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        items = list(reversed(rt.consigne.entries()))
        return page(request, "consigne.html", items=items, status_labels=STATUS_LABELS,
                    status_tone=STATUS_TONE, agent_labels=agent_control.LABELS, examples=EXAMPLES,
                    busy=any(i.status == "en_cours" for i in items))

    @app.post("/consigne")
    def consigne_send(texte: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            entry = rt.consigne.submit(texte, who.label)
        except ConsigneError as exc:
            return back("/consigne", f"Refusé : {exc}")
        return back("/consigne", {
            "repondue": "Réponse ci-dessous", "redirigee": "Voir la réponse ci-dessous",
        }.get(entry.status, "Consigne reçue : l'agent concerné s'en occupe"))

    @app.post("/consigne/{cid:int}/preciser")
    def consigne_answer(cid: int, precision: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.consigne.answer(cid, precision, who.label)
        except ConsigneError as exc:
            return back("/consigne", f"Refusé : {exc}")
        return back("/consigne", "Précision reçue : l'agent concerné s'en occupe")
