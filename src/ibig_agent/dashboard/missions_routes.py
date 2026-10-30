"""Pages « Missions » et « Supervision » (cahier des charges, sections 4, 5, 14 et 15).

Réservées à la direction et à l'administration : créer, approuver et lancer une mission, suspendre
un agent ou changer ses quotas engagent l'entreprise.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from .. import agent_control
from ..auth import Principal
from ..db import JournalEntry
from ..governance import AGENT_AUTONOMY, CHANNELS
from ..missions import AUTONOMIES, KINDS, STATUS_LABELS, MissionError
from ..prospecteur import parse_csv
from ..runtime import Runtime

FIELDS = ("name", "kind", "pole", "product", "zone", "target", "objective", "criteria", "agent",
          "channel", "autonomy", "budget_usd", "max_per_day", "max_total", "error_threshold")
AUTONOMY_TEXT = {"aucune": "N'agit jamais seul", "lecture": "Lecture seule",
                 "validation": "Propose, un humain valide",
                 "automatique": "Agit seul sur les cas simples"}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    # ------------------------------------------------------------------ missions
    @app.get("/missions", response_class=HTMLResponse)
    def missions(request: Request, statut: str = "", who: Principal = Depends(user)):
        direction_only(who)
        rows = rt.missions.all_missions(statut if statut in STATUS_LABELS else "")
        return page(request, "missions.html", rows=rows, statut=statut, labels=STATUS_LABELS,
                    poles=rt.org.poles, kinds=KINDS, autonomies=AUTONOMIES, channels=CHANNELS,
                    agents=agent_control.AGENTS, agent_labels=agent_control.LABELS,
                    runners=set(rt.missions.runners))

    @app.post("/missions")
    async def create_mission(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        form = await request.form()
        try:
            mid = rt.missions.create({k: form.get(k, "") for k in FIELDS}, who.label)
        except MissionError as exc:
            return back("/missions", f"Refusé : {exc}")
        return back(f"/missions/{mid}", "Brouillon enregistré : lancez la simulation")

    @app.get("/missions/{mid:int}", response_class=HTMLResponse)
    def mission(request: Request, mid: int, who: Principal = Depends(user)):
        direction_only(who)
        m = rt.missions.get(mid)
        if m is None:
            raise HTTPException(404)
        checks = rt.missions.checks(m)
        return page(request, "mission.html", m=m, events=rt.missions.events(mid), checks=checks,
                    failures=rt.missions.blocking_failures(checks), labels=STATUS_LABELS,
                    poles=rt.org.poles, kinds=KINDS, autonomies=AUTONOMIES, channels=CHANNELS,
                    agents=agent_control.AGENTS, agent_labels=agent_control.LABELS)

    @app.post("/missions/{mid:int}/modifier")
    async def edit_mission(request: Request, mid: int, who: Principal = Depends(user)):
        direction_only(who)
        form = await request.form()
        try:
            rt.missions.update(mid, {k: form.get(k, "") for k in FIELDS}, who.label)
        except MissionError as exc:
            return back(f"/missions/{mid}", f"Refusé : {exc}")
        return back(f"/missions/{mid}", "Mission modifiée : relancez la simulation")

    @app.post("/missions/{mid:int}/fichier")
    async def mission_file(mid: int, fichier: Annotated[UploadFile, File()],
                           who: Principal = Depends(user)):
        """Dépôt d'un CSV de prospects (export de réseau, d'annuaire, liste achetée)."""
        direction_only(who)
        m = rt.missions.get(mid)
        if m is None:
            raise HTTPException(404)
        if m.kind != "prospection":
            return back(f"/missions/{mid}", "Refusé : cette mission n'est pas une prospection")
        raw = await fichier.read(5_000_001)
        if len(raw) > 5_000_000:
            return back(f"/missions/{mid}", "Refusé : fichier de plus de 5 Mo")
        rows = parse_csv(raw.decode("utf-8-sig", errors="replace"))
        if not rows:
            return back(f"/missions/{mid}", "Refusé : aucune adresse e-mail reconnue "
                                            "(colonne « email » attendue)")
        folder = rt.settings.prospect_dir
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"mission_{mid}.csv").write_bytes(raw)
        rt.missions.note(mid, f"Fichier déposé : {len(rows)} adresse(s) à traiter", who.label)
        return back(f"/missions/{mid}", f"{len(rows)} adresse(s) reconnue(s) : traitées au "
                                        "prochain passage de la mission en cours")

    @app.post("/missions/{mid:int}/{action}")
    def mission_action(mid: int, action: str, motif: str = Form(""),
                       who: Principal = Depends(user)):
        direction_only(who)
        svc = rt.missions
        steps = {
            "simuler": (lambda: svc.simulate(mid, who.label), "Simulation faite (aucun contact)"),
            "approuver": (lambda: svc.approve(mid, who.label), "Mission approuvée"),
            "lancer": (lambda: svc.launch(mid, who.label), "Mission lancée"),
            "pause": (lambda: svc.pause(mid, who.label, motif), "Mission en pause"),
            "reprendre": (lambda: svc.resume(mid, who.label), "Mission reprise"),
            "arreter": (lambda: svc.stop(mid, who.label), "Mission arrêtée"),
        }
        if action not in steps:
            raise HTTPException(404)
        fn, ok = steps[action]
        try:
            fn()
        except MissionError as exc:
            return back(f"/missions/{mid}", f"Refusé : {exc}")
        return back(f"/missions/{mid}", ok)

    # ------------------------------------------------------------------ supervision
    @app.get("/supervision", response_class=HTMLResponse)
    def supervision(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        ctl = agent_control.controls(rt.sessions)
        data = []
        for agent in agent_control.AGENTS:
            with rt.sessions() as s:
                last = s.scalars(select(JournalEntry).where(
                    JournalEntry.agent == agent).order_by(JournalEntry.id.desc()).limit(1)).first()
                total = s.scalar(select(func.count()).select_from(JournalEntry).where(
                    JournalEntry.agent == agent)) or 0
            c = ctl.get(agent)
            use = agent_control.usage_today(rt.sessions, agent)
            data.append({
                "agent": agent, "label": agent_control.LABELS[agent], "last": last, "total": total,
                "autonomy": AUTONOMY_TEXT.get(AGENT_AUTONOMY.get(agent, ""), "Pas encore actif"),
                "suspended": bool(c and c.suspended), "reason": c.reason if c else "",
                "runs_cap": c.runs_cap_day if c else None, "cost_cap": c.cost_cap_day if c else None,
                "runs": use.runs, "cost": use.cost,
                "blocked": agent_control.blocked_reason(rt.sessions, agent)})
        return page(request, "supervision.html", rows=data)

    def supervise(who: Principal, fn, ok: str):
        direction_only(who)
        try:
            fn()
        except ValueError as exc:
            return back("/supervision", f"Refusé : {exc}")
        return back("/supervision", ok)

    @app.post("/supervision/{agent}/suspendre")
    def suspend(agent: str, motif: str = Form(""), who: Principal = Depends(user)):
        return supervise(who, lambda: agent_control.set_suspended(
            rt.sessions, agent, True, who.label, motif), f"Agent {agent} suspendu")

    @app.post("/supervision/{agent}/reprendre")
    def resume(agent: str, who: Principal = Depends(user)):
        return supervise(who, lambda: agent_control.set_suspended(
            rt.sessions, agent, False, who.label), f"Agent {agent} repris")

    @app.post("/supervision/{agent}/quotas")
    def quotas(agent: str, actions: str = Form(""), cout: str = Form(""),
               who: Principal = Depends(user)):
        def apply():
            try:
                runs = int(actions) if actions.strip() else None
                cost = float(cout.replace(",", ".")) if cout.strip() else None
            except ValueError as exc:
                raise ValueError("Les plafonds sont des nombres") from exc
            agent_control.set_quotas(rt.sessions, agent, runs, cost, who.label)

        return supervise(who, apply, f"Quotas de {agent} enregistrés")
