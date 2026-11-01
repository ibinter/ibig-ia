"""Page « Bases de données » : saisir les accès (chiffrés, jamais réaffichés) et les contrôles en
lecture seule (cahier des charges : détecter les anomalies des plateformes).

Réservée à la direction et à l'administration. Voir `dbmonitor.py` pour les garde-fous.
"""

from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import core_checks
from ..auth import Principal
from ..dbmonitor import DEFAULT_PORTS, KINDS, DbMonitorError
from ..runtime import Runtime


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    running: set[str] = set()

    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/donnees", response_class=HTMLResponse)
    def donnees_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        mon = rt.dbmonitor
        return page(request, "donnees.html", connections=mon.connection_names(),
                    checks=mon.checks(), kinds=KINDS, ports=DEFAULT_PORTS,
                    checking=bool(running))

    @app.post("/donnees/connexion")
    def add_connection(nom: str = Form(""), type: str = Form("mysql"), hote: str = Form(""),
                       port: int = Form(0), base: str = Form(""), utilisateur: str = Form(""),
                       mot_de_passe: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.dbmonitor.add_connection(nom, type, hote, port, base, utilisateur, mot_de_passe,
                                        who.label)
        except DbMonitorError as exc:
            return back("/donnees", f"Refusé : {exc}")
        return back("/donnees", "Accès enregistré (chiffré). Testez-le avant d'ajouter des contrôles")

    @app.post("/donnees/connexion/{nom}/tester")
    def test_connection(nom: str, who: Principal = Depends(user)):
        direction_only(who)
        try:
            return back("/donnees", rt.dbmonitor.test(nom))
        except DbMonitorError as exc:
            return back("/donnees", f"Refusé : {exc}")

    @app.post("/donnees/connexion/{nom}/catalogue")
    def install_catalog(nom: str, who: Principal = Depends(user)):
        direction_only(who)
        try:
            r = core_checks.install(rt.dbmonitor, nom, who.label)
        except DbMonitorError as exc:
            return back("/donnees", f"Refusé : {exc}")
        return back("/donnees", f"Contrôles d'IBIG CORE : {r['added']} ajouté(s), {r['skipped']} déjà présent(s)")

    @app.post("/donnees/connexion/{nom}/supprimer")
    def remove_connection(nom: str, who: Principal = Depends(user)):
        direction_only(who)
        rt.dbmonitor.remove_connection(nom, who.label)
        return back("/donnees", "Accès et contrôles associés supprimés")

    @app.post("/donnees/controle")
    def add_check(connexion: str = Form(""), nom: str = Form(""), requete: str = Form(""),
                  seuil: str = Form("0"), gravite: str = Form("warning"),
                  who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.dbmonitor.add_check(connexion, nom, requete, float(seuil.replace(",", ".") or 0),
                                   gravite, who.label)
        except (DbMonitorError, ValueError) as exc:
            return back("/donnees", f"Refusé : {exc}")
        return back("/donnees", "Contrôle enregistré")

    @app.post("/donnees/controle/{cid:int}/supprimer")
    def remove_check(cid: int, who: Principal = Depends(user)):
        direction_only(who)
        rt.dbmonitor.remove_check(cid, who.label)
        return back("/donnees", "Contrôle supprimé")

    @app.post("/donnees/executer")
    def run_now(who: Principal = Depends(user)):
        direction_only(who)
        if running:
            return back("/donnees", "Un contrôle est déjà en cours : patientez")
        running.add("x")

        def work() -> None:
            try:
                rt.dbmonitor.run()
            finally:
                running.discard("x")

        threading.Thread(target=work, daemon=True).start()
        return back("/donnees", "Contrôles lancés : rechargez la page dans une minute")
