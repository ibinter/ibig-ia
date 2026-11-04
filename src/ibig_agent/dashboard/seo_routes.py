"""Pages « Sites » (administration centrale du groupe) et « Référencement » (SEO de tous les sites).

Réservées à la direction. Lecture seule chez les sites : les corrections passent par une mission.
"""

from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..auth import Principal
from ..autopilot import AutopilotError, PlannedTask
from ..runtime import Runtime
from .ecosysteme_routes import STATE_LABEL, STATE_TONE

GRADE_TONE = {"excellent": "green", "bon": "green", "moyen": "amber", "faible": "red"}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    running: set[str] = set()

    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/sites", response_class=HTMLResponse)
    def sites_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        eco = rt.ecosystem.summary()
        seo = {r["asset"].id: r for r in rt.seo.overview()}
        rows = []
        for a in eco["assets"]:
            s = seo.get(a["id"])
            rows.append({**a, "seo": s["result"] if s else None, "seo_sev": s["sev"] if s else None})
        by_pole: dict[str, list] = {}
        for r in rows:
            by_pole.setdefault(r["pole"] or "Sans pôle", []).append(r)
        return page(request, "sites.html", by_pole=dict(sorted(by_pole.items())), eco=eco,
                    state_tone=STATE_TONE, state_label=STATE_LABEL, grade_tone=GRADE_TONE)

    @app.get("/seo", response_class=HTMLResponse)
    def seo_page(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        rows = rt.seo.overview()
        scores = [r["result"]["score"] for r in rows if r["result"]]
        return page(request, "seo.html", rows=rows, grade_tone=GRADE_TONE, checking=bool(running),
                    average=round(sum(scores) / len(scores)) if scores else None,
                    audited=len(scores), total=len(rows))

    @app.get("/seo/{aid:int}", response_class=HTMLResponse)
    def seo_detail(aid: int, request: Request, who: Principal = Depends(user)):
        direction_only(who)
        asset = next((a for a in rt.ecosystem.assets() if a.id == aid), None)
        if asset is None:
            raise HTTPException(404)
        result = rt.seo.result(aid)
        order = {"critical": 0, "warning": 1, "info": 2}
        issues = sorted((result or {}).get("issues", []), key=lambda i: order[i["severity"]])
        return page(request, "seo_detail.html", asset=asset, r=result, issues=issues, grade_tone=GRADE_TONE)

    @app.post("/seo/auditer")
    def audit_all(who: Principal = Depends(user)):
        direction_only(who)
        if running:
            return back("/seo", "Un audit est déjà en cours : patientez")
        running.add("x")

        def work() -> None:
            try:
                rt.seo.run()
            finally:
                running.discard("x")

        threading.Thread(target=work, daemon=True).start()
        return back("/seo", "Audit lancé : les notes apparaissent dans une minute environ")

    @app.post("/seo/{aid:int}/auditer")
    def audit_one(aid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.seo.audit_site(aid)
        except ValueError as exc:
            return back("/seo", f"Refusé : {exc}")
        return back(f"/seo/{aid}", "Audit terminé")

    @app.post("/seo/{aid:int}/mission")
    def seo_mission(aid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            title, goal, steps = rt.seo.fix_plan(aid)
            mid = rt.autopilot.create_from(
                title, goal, [PlannedTask(t, d, "", [n] if n else []) for n, (t, d) in enumerate(steps)],
                "", who.label, f"Audit de référencement du site n°{aid}")
        except (ValueError, AutopilotError) as exc:
            return back(f"/seo/{aid}", f"Refusé : {exc}")
        return back(f"/missions/{mid}", "Mission de référencement créée : validez puis suivez les étapes")
