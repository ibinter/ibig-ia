"""Tableau de bord : le seul endroit où les humains interviennent (section 5).

Ils y donnent les objectifs, valident (niveau 2), traitent les dossiers réservés
(niveau 3), consultent le journal et actionnent le bouton d'arrêt.
"""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import desc, select

from ..auth import Principal, UserStore, make_session, read_session
from ..channels.mail import MailMessage
from ..channels.web import sanitize_html
from ..config import Mailbox
from ..db import JournalEntry, PendingAction, Prospect, Ticket
from ..governance import ALL_CHANNELS, CHANNELS, GovernanceError
from ..runtime import Runtime
from ..scheduler import _safe
from . import config_routes
from .agents_view import agent_cards, runners
from .setup import progress, setup_steps
from .stats import home_stats, nav_counts

HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))
# Aperçu des articles : toujours re-nettoyé (le HTML a pu être modifié par un valideur).
TEMPLATES.env.filters["apercu"] = lambda html: Markup(sanitize_html(html or ""))
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.",
        "nov.", "déc."]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def install_filters(tz: ZoneInfo) -> None:
    """Dates en français, à l'heure locale d'IBIG."""
    env = TEMPLATES.env

    def local(dt: datetime | None) -> datetime | None:
        return _aware(dt).astimezone(tz) if dt else None

    def quand(dt: datetime | None, heure: bool = True) -> str:
        if not dt:
            return ""
        d = local(dt)
        txt = f"{d.day} {MOIS[d.month - 1]}"
        if d.year != datetime.now(tz).year:
            txt += f" {d.year}"
        return f"{txt} · {d:%H:%M}" if heure else txt

    def depuis(dt: datetime | None) -> str:
        if not dt:
            return ""
        sec = int((datetime.now(UTC) - _aware(dt)).total_seconds())
        if sec < 60:
            return "à l'instant"
        if sec < 3600:
            return f"il y a {sec // 60} min"
        if sec < 86400:
            return f"il y a {sec // 3600} h"
        if sec < 7 * 86400:
            return f"il y a {sec // 86400} j"
        return quand(dt, heure=False)

    def jour_court(dt: datetime) -> str:
        return f"{JOURS[dt.weekday()][:3]}. {dt.day}"

    def initiales(nom: str) -> str:
        mots = [m for m in (nom or "?").replace("-", " ").split() if m[:1].isalnum()]
        return "".join(m[0] for m in mots[:2]).upper() or "?"

    statuts = {"executed": "exécuté", "pending": "à valider", "prepared": "niveau 3",
               "failed": "échec", "rejected": "rejeté", "approved": "approuvé",
               "handled": "traité", "blocked": "bloqué", "flagged": "signalé",
               "queued": "en file", "gagne": "gagné", "qualifie": "qualifié",
               "resolu": "résolu", "en_cours": "en cours"}
    env.filters["statut"] = lambda v: statuts.get(v, (v or "").replace("_", " "))
    env.filters.update(local=local, quand=quand, depuis=depuis, jour_court=jour_court,
                       initiales=initiales)
    env.globals["aujourdhui"] = lambda: (
        f"{JOURS[datetime.now(tz).weekday()]} {datetime.now(tz).day} "
        f"{MOIS[datetime.now(tz).month - 1].rstrip('.')} {datetime.now(tz).year}")
SESSION_COOKIE = "ibig_session"
EDITABLE_KEYS = ("body", "texte", "contenu_html")
SARA_MAX_QUESTION = 2000
SARA_RATE = (20, 600)          # 20 questions par 10 minutes et par conversation
SARA_GLOBAL_RATE = (600, 600)  # plafond global (protège aussi le budget IA)
SARA_TRANSFER = ("Je transmets votre question à un conseiller, qui vous répondra dans les "
                 "meilleurs délais.")
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


class NotLoggedIn(Exception):
    pass


def create_app(rt: Runtime) -> FastAPI:
    if len(rt.settings.secret_key) < 32:
        raise RuntimeError("IBIG_SECRET_KEY doit faire au moins 32 caractères")
    app = FastAPI(title="Agent IA IBIG — tableau de bord", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    install_filters(ZoneInfo(rt.settings.timezone))
    users = UserStore(rt.sessions, rt.org)
    secret = rt.settings.secret_key
    failed_logins: dict[str, deque[float]] = defaultdict(deque)
    sara_calls: dict[str, deque[float]] = defaultdict(deque)

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
        who = getattr(request.state, "user", None)
        badges = {}
        if who is not None:
            with rt.sessions() as s:
                badges = nav_counts(s, who)
            if who.role != "valideur":
                done, total = progress(setup_steps(rt))
                badges["demarrage"] = total - done
        return TEMPLATES.TemplateResponse(request, name, {
            "user": who, "badges": badges, "path": request.url.path,
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
        # Cloisonnement : un valideur ne voit que les chiffres de ses pôles.
        scoped = who.role == "valideur"
        with rt.sessions() as s:
            stats = home_stats(s, who, rt.settings.monthly_ai_budget_usd)
        # La synthèse couvre tous les pôles (expéditeurs compris) : réservée à la direction.
        report = None if scoped else rt.chef.build_daily_report().as_text()
        steps = [] if scoped else setup_steps(rt)
        return page(request, "index.html", stats=stats, states=rt.governor.channel_states(),
                    channels=CHANNELS, report=report, steps=steps,
                    setup=progress(steps) if steps else (0, 0))

    trials: dict[int, deque[float]] = defaultdict(deque)
    running: set[str] = set()

    @app.get("/agents", response_class=HTMLResponse)
    def agents(request: Request, who: Principal = Depends(user)):
        accounts = [(i, a) for i, a in enumerate(rt.org.social_accounts)
                    if a.pole in rt.org.pole_codes]
        return page(request, "agents.html", cards=agent_cards(rt), accounts=accounts,
                    running=running, can_run=who.role != "valideur")

    @app.post("/agents/lancer/{key}")
    def run_agent(key: str, tasks: BackgroundTasks, who: Principal = Depends(user)):
        jobs = runners(rt)
        if key not in jobs:
            raise HTTPException(404)
        if who.role == "valideur":
            return back("/agents", "Refusé : les agents se lancent depuis un compte direction")
        if key in running:
            return back("/agents", "Déjà en cours : patientez quelques instants")
        fn, msg = jobs[key]

        def job():
            running.add(key)
            try:
                _safe(f"{key} (tableau de bord, {who.email})", fn)()
            finally:
                running.discard(key)

        tasks.add_task(job)
        return back("/agents", msg)

    @app.post("/agents/publication")
    def write_post(compte: int = Form(...), sujet: str = Form(...), jour: str = Form(""),
                   who: Principal = Depends(user)):
        if who.role == "valideur":
            return back("/agents", "Refusé : réservé à la direction")
        accounts = rt.org.social_accounts
        if not 0 <= compte < len(accounts) or not sujet.strip() or rt.llm is None:
            return back("/agents", "Refusé : choisissez un compte et un sujet")
        account = accounts[compte]
        try:
            today = datetime.now(ZoneInfo(rt.settings.timezone)).date()
            day = date.fromisoformat(jour) if jour else today + timedelta(days=1)
            pid = rt.communication.write_post(account.pole, account, sujet.strip()[:300], day)
        except Exception as exc:  # noqa: BLE001 — affiché, rien n'est publié
            return back("/agents", f"Échec de la rédaction : {exc}")
        if pid is None:
            return back("/agents", "Échec : publication non créée")
        return back("/validations", "Publication rédigée : relisez-la ci-dessous puis validez")

    @app.get("/essai", response_class=HTMLResponse)
    def trial_form(request: Request, who: Principal = Depends(user)):
        return page(request, "essai.html", poles=rt.org.poles, result=None, form={})

    @app.post("/essai", response_class=HTMLResponse)
    def trial(request: Request, message: str = Form(...), objet: str = Form(""),
              expediteur: str = Form(""), pole: str = Form("SOFT"),
              who: Principal = Depends(user)):
        form = {"message": message[:6000], "objet": objet[:300],
                "expediteur": expediteur[:200], "pole": pole}
        ctx = {"poles": rt.org.poles, "form": form, "result": None, "error": ""}
        now, calls = time.monotonic(), trials[who.id]
        while calls and now - calls[0] > 3600:
            calls.popleft()
        if pole not in rt.org.pole_codes or not message.strip():
            ctx["error"] = "Choisissez un pôle et collez un message."
        elif rt.llm is None:
            ctx["error"] = "L'intelligence artificielle n'est pas branchée (clé API manquante)."
        elif len(calls) >= 30:
            ctx["error"] = "Limite de 30 essais par heure atteinte : réessayez plus tard."
        else:
            calls.append(now)
            box = Mailbox(adresse=f"essai-{pole.lower()}@tableau-de-bord", hebergeur="lws",
                          pole=pole)
            msg = MailMessage(mailbox=box.adresse, message_id="<essai>", ref="essai",
                              sender=form["expediteur"] or "client@exemple.ci",
                              sender_name="", subject=form["objet"] or "(sans objet)",
                              body=form["message"])
            try:
                ctx["result"] = rt.messagerie.simulate(msg, box)
            except Exception as exc:  # noqa: BLE001 — affiché à l'utilisateur, rien d'envoyé
                ctx["error"] = f"L'essai n'a pas abouti : {exc}"
        return page(request, "essai.html", **ctx)

    config_routes.register(app, rt, user, page, back)

    @app.get("/demarrage", response_class=HTMLResponse)
    def getting_started(request: Request, who: Principal = Depends(user)):
        steps = setup_steps(rt)
        return page(request, "demarrage.html", steps=steps, setup=progress(steps))

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
        q = select(JournalEntry).order_by(desc(JournalEntry.id)).limit(min(limit, 1000))
        if who.role == "valideur":  # entrées de ses pôles seulement
            q = q.where(JournalEntry.pole.in_(who.poles))
        with rt.sessions() as s:
            rows = s.scalars(q).all()
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
            rows = s.scalars(select(Prospect).order_by(desc(Prospect.score),
                                                       desc(Prospect.updated_at))).all()
        if who.role == "valideur":  # données personnelles : chacun voit ses pôles
            rows = [p for p in rows if p.pole in who.poles]
        return page(request, "prospects.html", rows=rows)

    # ---------------------------------------------------------------- support
    @app.get("/tickets", response_class=HTMLResponse)
    def tickets(request: Request, tous: int = 0, who: Principal = Depends(user)):
        with rt.sessions() as s:
            q = select(Ticket).order_by(desc(Ticket.id)).limit(200)
            if not tous:
                q = q.where(Ticket.status == "ouvert")
            rows = s.scalars(q).all()
        if who.role == "valideur":
            rows = [t for t in rows if t.pole in who.poles]
        return page(request, "tickets.html", rows=rows, tous=tous)

    @app.post("/tickets/{tid}/resolu")
    def ticket_resolved(tid: int, who: Principal = Depends(user)):
        with rt.sessions() as s:
            t = s.get(Ticket, tid)
        if t is None:
            raise HTTPException(404)
        if who.role == "valideur" and t.pole not in who.poles:
            return back("/tickets", f"Refusé : vous ne suivez pas le pôle {t.pole}")
        try:
            rt.support.resolve(tid, by=who.label)
        except ValueError as exc:
            return back("/tickets", f"Refusé : {exc}")
        return back("/tickets", f"Ticket n° {tid} résolu")

    @app.post("/api/sara/question")
    async def sara_question(request: Request):
        """API de SARA, à appeler depuis le serveur des solutions (clé secrète)."""
        key = rt.settings.sara_api_key
        if len(key) < 32:
            return JSONResponse({"erreur": "API SARA non configurée"}, status_code=503)
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth.encode(), f"Bearer {key}".encode()):
            return JSONResponse({"erreur": "clé invalide"}, status_code=401)
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"erreur": "JSON invalide"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"erreur": "objet JSON attendu"}, status_code=400)
        question = str(data.get("question", "")).strip()
        pole = str(data.get("pole") or "SOFT")
        conversation = str(data.get("conversation", ""))[:200]
        if not question or len(question) > SARA_MAX_QUESTION:
            return JSONResponse({"erreur": "question vide ou trop longue"}, status_code=400)
        if pole not in rt.org.pole_codes:
            return JSONResponse({"erreur": f"pôle inconnu : {pole}"}, status_code=400)
        # L'appelant est le serveur du site : on limite par conversation du chat
        # (l'adresse IP serait la même pour tous les clients), plus un plafond global.
        client = request.client.host if request.client else "?"
        now = time.monotonic()
        for bucket, (limit, window) in ((f"conv:{conversation or client}", SARA_RATE),
                                        ("*", SARA_GLOBAL_RATE)):
            calls = sara_calls[bucket]
            while calls and now - calls[0] > window:
                calls.popleft()
            if len(calls) >= limit:
                return JSONResponse({"erreur": "trop de questions, réessayez plus tard"},
                                    status_code=429)
        sara_calls[f"conv:{conversation or client}"].append(now)
        sara_calls["*"].append(now)
        result = rt.support.handle_chat(question, pole,
                                        contact=str(data.get("contact", ""))[:300],
                                        conversation=conversation)
        answered = result.ticket_id is None
        return JSONResponse({
            "reponse": result.reponse if answered else SARA_TRANSFER,
            "sources": [{"titre": x["titre"], "section": x["section"]} for x in result.sources]
            if answered else [],
            "transmis": not answered,
            "ticket": result.ticket_id,
        })

    # ---------------------------------------------------------------- WhatsApp (Meta)
    @app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
    def whatsapp_verify(request: Request):
        """Vérification de l'abonnement par Meta (hub.challenge)."""
        q = request.query_params
        token = rt.settings.whatsapp_verify_token
        if (q.get("hub.mode") == "subscribe" and token
                and secrets.compare_digest(q.get("hub.verify_token", ""), token)):
            return q.get("hub.challenge", "")
        raise HTTPException(403)

    @app.post("/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request, tasks: BackgroundTasks):
        from ..channels.whatsapp import parse_webhook, verify_signature

        body = await request.body()
        if not rt.settings.whatsapp_app_secret:
            return JSONResponse({"erreur": "WhatsApp non configuré"}, status_code=503)
        if not verify_signature(rt.settings.whatsapp_app_secret, body,
                                request.headers.get("x-hub-signature-256", "")):
            return JSONResponse({"erreur": "signature invalide"}, status_code=401)
        try:
            import json

            messages = parse_webhook(json.loads(body))
        except ValueError:
            return JSONResponse({"erreur": "JSON invalide"}, status_code=400)
        # Réponse immédiate à Meta ; le traitement (tri, IA) se fait ensuite.
        agent = rt.whatsapp
        for m in messages:
            tasks.add_task(agent.handle, m)
        return {"recus": len(messages)}

    @app.post("/prospects/{pid}/statut")
    def prospect_status(pid: int, statut: str = Form(...), who: Principal = Depends(user)):
        with rt.sessions() as s:
            p = s.get(Prospect, pid)
        if p is None:
            raise HTTPException(404)
        if who.role == "valideur" and p.pole not in who.poles:
            return back("/prospects", f"Refusé : vous ne suivez pas le pôle {p.pole}")
        try:
            rt.commercial.set_status(pid, statut, by=who.label)
        except ValueError as exc:
            return back("/prospects", f"Refusé : {exc}")
        return back("/prospects", f"{p.email} : {statut}")

    @app.get("/rapports", response_class=HTMLResponse)
    def reports(request: Request, who: Principal = Depends(user)):
        if who.role == "valideur":  # rapports de direction, tous pôles confondus
            raise HTTPException(403)
        with rt.sessions() as s:
            rows = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "report.publish",
                JournalEntry.status == "executed").order_by(desc(JournalEntry.id))
                .limit(60)).all()
        return page(request, "rapports.html", rows=rows)

    @app.get("/indicateurs", response_class=HTMLResponse)
    def indicators(request: Request, jours: int = 7, who: Principal = Depends(user)):
        jours = min(max(jours, 1), 90)
        with rt.sessions() as s:
            alerts = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "veille.alert").order_by(desc(JournalEntry.id))
                .limit(30)).all()
        if who.role == "valideur":
            alerts = [a for a in alerts if not a.pole or a.pole in who.poles]
        return page(request, "indicateurs.html", jours=jours,
                    report=rt.veille.indicators(jours), alerts=alerts)

    @app.get("/utilisateurs", response_class=HTMLResponse)
    def accounts(request: Request, who: Principal = Depends(user)):
        if who.role != "admin":
            raise HTTPException(403)
        rows = [(u, rt.org.poles_of(u.email)) for u in users.all()]
        return page(request, "utilisateurs.html", rows=rows)

    return app
