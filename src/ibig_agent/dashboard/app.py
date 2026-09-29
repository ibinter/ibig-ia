"""Tableau de bord : le seul endroit où les humains interviennent (section 5).

Ils y donnent les objectifs, valident (niveau 2), traitent les dossiers réservés
(niveau 3), consultent le journal et actionnent le bouton d'arrêt.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from ..db import JournalEntry, PendingAction, ProcessedMessage, Prospect
from ..governance import ALL_CHANNELS, CHANNELS, GovernanceError
from ..runtime import Runtime

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TOKEN_COOKIE = "ibig_token"
USER_COOKIE = "ibig_user"
EDITABLE_KEYS = ("body", "texte")


class NotLoggedIn(Exception):
    pass


def create_app(rt: Runtime) -> FastAPI:
    app = FastAPI(title="Agent IA IBIG — tableau de bord", docs_url=None, redoc_url=None)

    @app.exception_handler(NotLoggedIn)
    async def _to_login(request: Request, exc: NotLoggedIn):
        return RedirectResponse("/login", status_code=303)

    def user(request: Request) -> str:
        token = request.cookies.get(TOKEN_COOKIE, "")
        name = request.cookies.get(USER_COOKIE, "")
        if not (token and name and secrets.compare_digest(token, rt.settings.dashboard_token)):
            raise NotLoggedIn()
        return name

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"user": request.cookies.get(
            USER_COOKIE, ""), "msg": request.query_params.get("msg", ""), **ctx})

    def back(url: str, msg: str) -> RedirectResponse:
        return RedirectResponse(f"{url}?msg={quote(msg)}", status_code=303)

    # ---------------------------------------------------------------- accès
    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        return "ok"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return page(request, "login.html")

    @app.post("/login")
    def login(nom: str = Form(...), token: str = Form(...)):
        if not secrets.compare_digest(token, rt.settings.dashboard_token) or not nom.strip():
            return back("/login", "Identifiants invalides")
        resp = RedirectResponse("/", status_code=303)
        for key, value in ((TOKEN_COOKIE, token), (USER_COOKIE, nom.strip())):
            resp.set_cookie(key, value, httponly=True, samesite="strict", max_age=12 * 3600)
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(TOKEN_COOKIE)
        resp.delete_cookie(USER_COOKIE)
        return resp

    # ---------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, who: str = Depends(user)):
        with rt.sessions() as s:
            counts = dict(s.execute(select(PendingAction.status, func.count())
                                    .group_by(PendingAction.status)).all())
            mails = s.scalar(select(func.count()).select_from(ProcessedMessage)) or 0
            prospects = s.scalar(select(func.count()).select_from(Prospect)) or 0
        report = rt.chef.build_daily_report()
        return page(request, "index.html", counts=counts, mails=mails, prospects=prospects,
                    states=rt.governor.channel_states(), report=report.as_text())

    @app.get("/validations", response_class=HTMLResponse)
    def validations(request: Request, who: str = Depends(user)):
        with rt.sessions() as s:
            pending = s.scalars(select(PendingAction).where(PendingAction.status == "pending")
                                .order_by(PendingAction.created_at)).all()
            prepared = s.scalars(select(PendingAction).where(PendingAction.status == "prepared")
                                 .order_by(PendingAction.created_at)).all()
        return page(request, "validations.html", pending=pending, prepared=prepared,
                    editable=EDITABLE_KEYS)

    @app.post("/validations/{pid}/approuver")
    def approve(pid: int, request: Request, contenu: str | None = Form(None),
                who: str = Depends(user)):
        override = None
        if contenu is not None:
            with rt.sessions() as s:
                pa = s.get(PendingAction, pid)
                if pa is None:
                    raise HTTPException(404)
                key = next((k for k in EDITABLE_KEYS if k in pa.payload), None)
                if key and contenu.strip() != pa.payload[key].strip():
                    override = {key: contenu, "modifie_par_valideur": True}
        try:
            out = rt.governor.approve(pid, by=who, payload_override=override)
        except GovernanceError as exc:
            return back("/validations", f"Refusé : {exc}")
        msg = "Validé et exécuté" if out.status == "executed" else f"Échec : {out.error}"
        return back("/validations", msg)

    @app.post("/validations/{pid}/rejeter")
    def reject(pid: int, motif: str = Form(""), who: str = Depends(user)):
        try:
            rt.governor.reject(pid, by=who, reason=motif)
        except GovernanceError as exc:
            return back("/validations", f"Refusé : {exc}")
        return back("/validations", "Rejeté")

    @app.post("/validations/{pid}/traite")
    def handled(pid: int, note: str = Form(""), who: str = Depends(user)):
        try:
            rt.governor.mark_handled(pid, by=who, note=note)
        except GovernanceError as exc:
            return back("/validations", f"Refusé : {exc}")
        return back("/validations", "Marqué comme traité")

    @app.get("/journal", response_class=HTMLResponse)
    def journal(request: Request, limit: int = 200, who: str = Depends(user)):
        with rt.sessions() as s:
            rows = s.scalars(select(JournalEntry).order_by(desc(JournalEntry.id))
                             .limit(min(limit, 1000))).all()
        return page(request, "journal.html", rows=rows)

    @app.get("/arret", response_class=HTMLResponse)
    def stop_page(request: Request, who: str = Depends(user)):
        return page(request, "arret.html", channels=[ALL_CHANNELS, *CHANNELS],
                    states=rt.governor.channel_states())

    @app.post("/arret")
    def stop(canal: str = Form(...), action: str = Form(...), motif: str = Form(""),
             who: str = Depends(user)):
        if canal not in (ALL_CHANNELS, *CHANNELS) or action not in ("stop", "reprise"):
            raise HTTPException(400)
        rt.governor.set_stopped(canal, action == "stop", by=who, reason=motif)
        label = "tout le périmètre" if canal == ALL_CHANNELS else canal
        return back("/arret", f"{label} : {'suspendu' if action == 'stop' else 'réactivé'}")

    @app.get("/prospects", response_class=HTMLResponse)
    def prospects(request: Request, who: str = Depends(user)):
        with rt.sessions() as s:
            rows = s.scalars(select(Prospect).order_by(desc(Prospect.updated_at))).all()
        return page(request, "prospects.html", rows=rows)

    return app
