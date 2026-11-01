"""Page « Écosystème » : le centre de contrôle de tous les sites et logiciels IBIG.

Réservée à la direction et à l'administration. L'état se rafraîchit seul toutes les 30 secondes.
Les corrections sont PROPOSÉES : cette page ne modifie rien chez les actifs surveillés.
"""

from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..autopilot import AutopilotError, PlannedTask
from ..ecosystem import (
    CRITICALITIES,
    HOSTINGS,
    KIND_LABELS,
    KINDS,
    SEVERITY_LABELS,
    EcosystemError,
)
from ..runtime import Runtime

STATE_TONE = {"ok": "green", "attention": "amber", "critique": "red", "inconnu": "blue", "inactif": ""}
STATE_LABEL = {"ok": "Normal", "attention": "À surveiller", "critique": "Urgent", "inconnu": "En attente",
               "inactif": "Non suivi"}
LEVEL_TONE = {"ok": "ok", "attention": "warn", "critique": "bad", "inconnu": "info"}


def spark_path(values: list[int], width: int = 96, height: int = 22) -> tuple[str, list[int]]:
    """Courbe de vitesse (SVG) des dernières mesures ; les échecs (-1) sont renvoyés à part."""
    good = [v for v in values if v >= 0]
    if len(values) < 2 or not good:
        return "", [i for i, v in enumerate(values) if v < 0]
    top = max(good) or 1
    step = width / (len(values) - 1)
    pts = []
    for i, v in enumerate(values):
        if v >= 0:
            pts.append(f"{i * step:.1f},{height - 2 - (v / top) * (height - 4):.1f}")
    return " ".join(pts), [round(i * step) for i, v in enumerate(values) if v < 0]


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    running: set[str] = set()

    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    def context() -> dict:
        data = rt.ecosystem.summary()
        incidents = rt.ecosystem.incidents()
        diags = rt.ecosystem.diagnoses([i.id for i in incidents])
        fixes = rt.ecosystem.missions_for([i.id for i in incidents])
        for a in data["assets"]:
            a["path"], a["fails"] = spark_path(a["spark"])
        data["tone"] = LEVEL_TONE[data["level"]]
        return {"eco": data, "server": rt.ecosystem.server_state(), "incidents": incidents,
                "heartbeat": rt.ecosystem.heartbeat_status(), "diags": diags, "fixes": fixes,
                "state_tone": STATE_TONE, "state_label": STATE_LABEL,
                "severity_labels": SEVERITY_LABELS, "kind_labels": KIND_LABELS,
                "ftp_summary": rt.ecosystem.ftp_summary()}

    @app.get("/ecosysteme", response_class=HTMLResponse)
    def ecosysteme_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        return page(request, "ecosysteme.html", **context(), kinds=KINDS, hostings=HOSTINGS,
                    criticalities=CRITICALITIES, poles=rt.org.poles, checking=bool(running))

    @app.get("/ecosysteme/live", response_class=HTMLResponse)
    def ecosysteme_live(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        return page(request, "_ecosysteme_live.html", **context())

    @app.post("/ecosysteme/sonder")
    def probe_now(who: Principal = Depends(user)):
        direction_only(who)
        if running:
            return back("/ecosysteme", "Une mesure est déjà en cours : patientez")
        running.add("x")

        def work() -> None:
            try:
                rt.ecosystem.run_probes()
                rt.ecosystem.run_hygiene()
            finally:
                running.discard("x")

        threading.Thread(target=work, daemon=True).start()
        return back("/ecosysteme", "Mesure lancée : la page se met à jour toute seule")

    @app.post("/ecosysteme/battement")
    def set_heartbeat(adresse: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.ecosystem.set_heartbeat_url(adresse, who.label)
        except EcosystemError as exc:
            return back("/ecosysteme", f"Refusé : {exc}")
        if adresse.strip():
            ok = rt.ecosystem.heartbeat()
            return back("/ecosysteme", "Surveillance externe enregistrée" if ok
                        else "Adresse enregistrée, mais le service ne répond pas : vérifiez-la")
        return back("/ecosysteme", "Surveillance externe retirée")

    @app.post("/ecosysteme/inventaire")
    def reload_inventory(who: Principal = Depends(user)):
        direction_only(who)
        r = rt.ecosystem.sync_config()
        if r["missing_file"]:
            return back("/ecosysteme", "Refusé : fichier d'inventaire introuvable")
        return back("/ecosysteme", f"Inventaire relu : {r['created']} ajouté(s), {r['updated']} mis à jour")

    @app.post("/ecosysteme/actif")
    def add_asset(nom: str = Form(""), url: str = Form(""), genre: str = Form("site"),
                  hebergement: str = Form("vps"), pole: str = Form(""), criticite: str = Form("moyenne"),
                  chemin: str = Form("/"), attendu: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.ecosystem.add_asset(nom, url, genre, hebergement, pole, criticite, chemin, attendu)
        except EcosystemError as exc:
            return back("/ecosysteme", f"Refusé : {exc}")
        return back("/ecosysteme", "Actif ajouté : il sera mesuré dans les 5 minutes")

    @app.post("/ecosysteme/import")
    def import_assets(liste: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        r = rt.ecosystem.import_text(liste)
        return back("/ecosysteme", f"{r['added']} actif(s) ajouté(s), {r['rejected']} ligne(s) refusée(s)")

    @app.post("/ecosysteme/actif/{aid:int}/{action}")
    def asset_action(aid: int, action: str, who: Principal = Depends(user)):
        direction_only(who)
        if action not in ("suivre", "ignorer"):
            raise HTTPException(404)
        try:
            rt.ecosystem.set_active(aid, action == "suivre")
        except EcosystemError as exc:
            return back("/ecosysteme", f"Refusé : {exc}")
        return back("/ecosysteme", "Actif suivi" if action == "suivre" else "Actif retiré de la surveillance")

    @app.post("/ecosysteme/incident/{iid:int}/{action}")
    def incident_action(iid: int, action: str, who: Principal = Depends(user)):
        direction_only(who)
        if action == "mission":
            try:
                title, goal, steps = rt.ecosystem.fix_plan(iid)
                mid = rt.autopilot.create_from(
                    title, goal, [PlannedTask(t, d, "", [n] if n else []) for n, (t, d) in enumerate(steps)],
                    "", who.label, f"Incident de l'écosystème n°{iid}")
            except (EcosystemError, AutopilotError) as exc:
                return back("/ecosysteme", f"Refusé : {exc}")
            rt.ecosystem.link_mission(iid, mid, who.label)
            return back(f"/missions/{mid}", "Mission de correction créée : validez puis suivez les étapes")
        if action == "analyser":
            try:
                rt.ecosystem.diagnose(iid, who.label)
            except EcosystemError as exc:
                return back("/ecosysteme", f"Refusé : {exc}")
            return back("/ecosysteme", "Analyse de l'IA ajoutée à l'incident")
        status = {"pris": "ack", "resolu": "resolved", "rouvrir": "open"}.get(action)
        if status is None:
            raise HTTPException(404)
        try:
            rt.ecosystem.set_incident_status(iid, status, who.label)
        except EcosystemError as exc:
            return back("/ecosysteme", f"Refusé : {exc}")
        return back("/ecosysteme", {"ack": "Pris en compte", "resolved": "Marqué résolu",
                                    "open": "Rouvert"}[status])

    @app.post("/ecosysteme/ftp")
    def ftp_save(label: str = Form(""), host: str = Form(""), port: str = Form("21"),
                 mode: str = Form("ftps"), login: str = Form(""), password: str = Form(""),
                 root_path: str = Form("/"), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.ecosystem.set_ftp_cred(label, host, int(port) if port.isdigit() else 21,
                                      mode, login, password, root_path, who.label)
        except EcosystemError as exc:
            return back("/ecosysteme", f"Refusé : {exc}")
        return back("/ecosysteme", f"Identifiants FTP « {label} » enregistrés")

    @app.post("/ecosysteme/ftp/{cid:int}/verifier")
    def ftp_check(cid: int, who: Principal = Depends(user)):
        direction_only(who)
        if running:
            return back("/ecosysteme", "Une mesure est déjà en cours : patientez")
        running.add("ftp")

        def work() -> None:
            try:
                rt.ecosystem.run_ftp_check(cid)
            except EcosystemError:
                pass
            finally:
                running.discard("ftp")

        threading.Thread(target=work, daemon=True).start()
        return back("/ecosysteme", "Vérification FTP lancée : résultat visible dans quelques secondes")

    @app.post("/ecosysteme/ftp/{cid:int}/supprimer")
    def ftp_delete(cid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.ecosystem.delete_ftp_cred(cid)
        except EcosystemError as exc:
            return back("/ecosysteme", f"Refusé : {exc}")
        return back("/ecosysteme", "Identifiants FTP supprimés")
