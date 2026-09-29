"""Tableau de bord : le seul endroit où les humains interviennent (section 5).

Ils y donnent les objectifs, valident (niveau 2), traitent les dossiers réservés
(niveau 3), consultent le journal et actionnent le bouton d'arrêt.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from ..auth import Principal, UserStore, make_session, read_session
from ..db import JournalEntry, PendingAction, ProcessedMessage, Prospect
from ..governance import ALL_CHANNELS, CHANNELS, GovernanceError
from ..runtime import Runtime

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION_COOKIE = "ibig_session"
EDITABLE_KEYS = ("body", "texte", "contenu_html")
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


class NotLoggedIn(Exception):
    pass


def create_app(rt: Runtime) -> FastAPI:
    if len(rt.settings.secret_key) < 32:
        raise RuntimeError("IBIG_SECRET_KEY doit faire au moins 32 caractères")
    app = FastAPI(title="Agent IA IBIG — tableau de bord", docs_url=None, redoc_url=None)
    users = UserStore(rt.sessions, rt.org)
    secret = rt.settings.secret_key
    failed_logins: dict[str, deque[float]] = defaultdict(deque)

    @app.exception_handler(NotLoggedIn)
    async def _to_login(request: Request, exc: NotLoggedIn):
        return RedirectResponse("/login", status_code=303)

    def user(request: Request) -> Principal:
        user_id = read_session(secret, request.cookies.get(SESSION_COOKIE, ""))
        principal = users.get(user_id) if user_id is not None else None
        if principal is None:
            raise NotLoggedIn()
        request.state.user = principal
        return principal

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {
            "user": getattr(request.state, "user", None),
            "msg": request.query_params.get("msg", ""), **ctx})

    def back(url: str, msg: str) -> RedirectResponse:
        return RedirectResponse(f"{url}?msg={quote(msg)}", status_code=303)

    def load(pid: int) -> PendingAction:
        with rt.sessions() as s:
            pa = s.get(PendingAction, pid)
        if pa is None:
            raise HTTPException(404)
        return pa

    # ---------------------------------------------------------------- accès
    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        return "ok"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return page(request, "login.html")

    @app.post("/login")
    def login(email: str = Form(...), password: str = Form(...)):
        key, now = email.strip().lower(), time.monotonic()
        attempts = failed_logins[key]
        while attempts and now - attempts[0] > LOCKOUT_SECONDS:
            attempts.popleft()
        if len(attempts) >= MAX_FAILED_LOGINS:
            return back("/login", "Trop de tentatives : réessayez dans 15 minutes")
        principal = users.authenticate(email, password)
        if principal is None:
            attempts.append(now)
            return back("/login", "Identifiants invalides")
        failed_logins.pop(key, None)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(SESSION_COOKIE, make_session(secret, principal.id), httponly=True,
                        samesite="strict", secure=rt.settings.dashboard_url.startswith("https"),
                        max_age=12 * 3600)
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ---------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, who: Principal = Depends(user)):
        with rt.sessions() as s:
            counts = dict(s.execute(select(PendingAction.status, func.count())
                                    .group_by(PendingAction.status)).all())
            mails = s.scalar(select(func.count()).select_from(ProcessedMessage)) or 0
            prospects = s.scalar(select(func.count()).select_from(Prospect)) or 0
        report = rt.chef.build_daily_report()
        return page(request, "index.html", counts=counts, mails=mails, prospects=prospects,
                    states=rt.governor.channel_states(), report=report.as_text())

    @app.get("/validations", response_class=HTMLResponse)
    def validations(request: Request, who: Principal = Depends(user)):
        with rt.sessions() as s:
            pending = s.scalars(select(PendingAction).where(PendingAction.status == "pending")
                                .order_by(PendingAction.created_at)).all()
            prepared = s.scalars(select(PendingAction).where(PendingAction.status == "prepared")
                                 .order_by(PendingAction.created_at)).all()
        pending = [pa for pa in pending if who.can_decide(pa)]
        prepared = prepared if who.can_handle_level3() else []
        return page(request, "validations.html", pending=pending, prepared=prepared,
                    editable=EDITABLE_KEYS)

    @app.post("/validations/{pid}/approuver")
    def approve(pid: int, contenu: str | None = Form(None), who: Principal = Depends(user)):
        pa = load(pid)
        if not who.can_decide(pa):
            return back("/validations", f"Refusé : vous ne validez pas le pôle {pa.pole}")
        override = None
        key = next((k for k in EDITABLE_KEYS if k in pa.payload), None)
        if contenu is not None and key and contenu.strip() != pa.payload[key].strip():
            override = {key: contenu, "modifie_par_valideur": True}
        try:
            out = rt.governor.approve(pid, by=who.label, payload_override=override)
        except GovernanceError as exc:
            return back("/validations", f"Refusé : {exc}")
        msg = "Validé et exécuté" if out.status == "executed" else f"Échec : {out.error}"
        return back("/validations", msg)

    @app.post("/validations/{pid}/rejeter")
    def reject(pid: int, motif: str = Form(""), who: Principal = Depends(user)):
        pa = load(pid)
        if not who.can_decide(pa):
            return back("/validations", f"Refusé : vous ne validez pas le pôle {pa.pole}")
        try:
            rt.governor.reject(pid, by=who.label, reason=motif)
        except GovernanceError as exc:
            return back("/validations", f"Refusé : {exc}")
        return back("/validations", "Rejeté")

    @app.post("/validations/{pid}/traite")
    def handled(pid: int, note: str = Form(""), who: Principal = Depends(user)):
        load(pid)
        if not who.can_handle_level3():
            return back("/validations", "Refusé : réservé à la direction")
        try:
            rt.governor.mark_handled(pid, by=who.label, note=note)
        except GovernanceError as exc:
            return back("/validations", f"Refusé : {exc}")
        return back("/validations", "Marqué comme traité")

    @app.get("/journal", response_class=HTMLResponse)
    def journal(request: Request, limit: int = 200, who: Principal = Depends(user)):
        with rt.sessions() as s:
            rows = s.scalars(select(JournalEntry).order_by(desc(JournalEntry.id))
                             .limit(min(limit, 1000))).all()
        return page(request, "journal.html", rows=rows)

    @app.get("/arret", response_class=HTMLResponse)
    def stop_page(request: Request, who: Principal = Depends(user)):
        return page(request, "arret.html", channels=[ALL_CHANNELS, *CHANNELS],
                    states=rt.governor.channel_states())

    @app.post("/arret")
    def stop(canal: str = Form(...), action: str = Form(...), motif: str = Form(""),
             who: Principal = Depends(user)):
        if canal not in (ALL_CHANNELS, *CHANNELS) or action not in ("stop", "reprise"):
            raise HTTPException(400)
        # Tout le monde peut arrêter ; seules l'administration et la direction relancent.
        if action == "reprise" and not who.can_resume():
            return back("/arret", "Refusé : la réactivation est réservée à la direction")
        rt.governor.set_stopped(canal, action == "stop", by=who.label, reason=motif)
        label = "tout le périmètre" if canal == ALL_CHANNELS else canal
        return back("/arret", f"{label} : {'suspendu' if action == 'stop' else 'réactivé'}")

    @app.get("/prospects", response_class=HTMLResponse)
    def prospects(request: Request, who: Principal = Depends(user)):
        with rt.sessions() as s:
            rows = s.scalars(select(Prospect).order_by(desc(Prospect.updated_at))).all()
        if who.role == "valideur":  # données personnelles : chacun voit ses pôles
            rows = [p for p in rows if p.pole in who.poles]
        return page(request, "prospects.html", rows=rows)

    @app.get("/utilisateurs", response_class=HTMLResponse)
    def accounts(request: Request, who: Principal = Depends(user)):
        if who.role != "admin":
            raise HTTPException(403)
        rows = [(u, rt.org.poles_of(u.email)) for u in users.all()]
        return page(request, "utilisateurs.html", rows=rows)

    return app
