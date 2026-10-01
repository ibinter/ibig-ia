"""Tableau de bord : le seul endroit où les humains interviennent (section 5).

Ils y donnent les objectifs, valident (niveau 2), traitent les dossiers réservés
(niveau 3), consultent le journal et actionnent le bouton d'arrêt.
"""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import desc, func, select

from ..auth import (
    MIN_PASSWORD,
    SESSION_SECONDS,
    Principal,
    UserStore,
    make_session,
    read_action_token,
    read_session_full,
)
from ..channels.mail import MailMessage
from ..channels.web import sanitize_html
from ..config import Mailbox
from ..configstore import service_value
from ..db import JobStatus, JournalEntry, MediaAsset, PendingAction, Prospect, Ticket, User
from ..governance import ALL_CHANNELS, CHANNELS, GovernanceError
from ..runtime import Runtime
from ..scheduler import _safe
from . import config_routes, social_routes
from .agents_view import agent_cards, runners
from .setup import progress, setup_steps
from .stats import home_stats, nav_counts
from .visuals import first_sentence, render_png, render_svg

# Libellés et icônes des actions de l'agent, pour les listes lisibles par tous
ACTION_LABELS = {
    "mail.label": "Mail classé", "mail.ack": "Accusé de réception",
    "mail.faq_reply": "Réponse FAQ envoyée", "mail.reply": "Réponse par mail",
    "mail.forward_internal": "Transfert en interne", "support.answer": "Réponse du Support",
    "sara.answer": "Réponse de SARA", "whatsapp.reply": "Réponse WhatsApp",
    "whatsapp.ack": "Accusé WhatsApp", "whatsapp.faq_reply": "Réponse FAQ WhatsApp",
    "whatsapp.support_answer": "Réponse Support WhatsApp", "social.post": "Publication",
    "social.manual_post": "Publication (à la main)", "social.schedule_approved":
    "Publication programmée", "web.article_draft": "Article ou page web",
    "campaign.mail": "Campagne mail", "commercial.followup": "Relance commerciale",
    "notify.internal": "Message à l'équipe", "notify.whatsapp": "Alerte WhatsApp",
    "report.publish": "Rapport", "veille.alert": "Alerte de veille",
    "budget.alert": "Alerte budget", "security.suspicious_message": "Message suspect",
    "complaint.serious": "Réclamation grave", "whatsapp.manual": "WhatsApp à traiter",
    "legal": "Juridique", "contract": "Contrat", "refund": "Remboursement",
    "payment": "Paiement", "discount": "Remise", "pricing.unpublished": "Tarif hors grille",
    "media.reply": "Presse", "crisis": "Crise",
    "support.ticket": "Ticket de support", "commercial.alert": "Alerte commerciale",
    "commercial.qualify": "Prospect qualifié", "commercial.handoff": "Transmis au Commercial",
    "prospect.gagne": "Prospect gagné", "prospect.perdu": "Prospect perdu",
    "prospect.stop": "Relances arrêtées", "prospect.reprise": "Relances reprises",
    "channel.stop": "Canal suspendu", "channel.resume": "Canal réactivé",
    "system.job_failed": "Tâche automatique en échec",
    "kb.import": "Import depuis les sites", "approval.stale": "Validation en retard (24 h)",
    "killswitch.stop": "Bouton d'arrêt", "mail.manual_triage": "Mail à trier à la main",
    "privacy.erase": "Effacement de données", "privacy.purge": "Purge des données",
    "prospect.opt_out": "Désinscription", "support.resolve": "Ticket résolu",
}
ACTION_ICONS = {"mail": "mail", "support": "life", "sara": "bot", "whatsapp": "message",
                "social": "share", "web": "globe", "campaign": "mail", "commercial": "target",
                "notify": "users", "report": "file", "veille": "eye", "budget": "wallet",
                "security": "shield", "complaint": "alert", "channel": "power", "killswitch": "power",
                "approval": "clock", "privacy": "lock", "prospect": "target", "kb": "book",
                "legal": "hand", "contract": "hand", "refund": "wallet", "payment": "wallet",
                "discount": "wallet", "pricing": "wallet", "media": "alert", "crisis": "alert",
                "system": "alert"}

NETWORK_NAMES = {"facebook_page": "Page Facebook", "facebook_groupe": "Groupe Facebook",
                 "instagram": "Instagram", "threads": "Threads", "linkedin": "LinkedIn",
                 "tiktok": "TikTok", "whatsapp_chaine": "Chaîne WhatsApp", "x": "X"}
# Familles pour filtrer les validations
FAMILIES = {"mail": "Mails", "commercial": "Mails", "support": "Mails", "whatsapp": "WhatsApp",
            "social": "Publications", "web": "Articles web", "campaign": "Campagnes"}


def lisible(text: str) -> str:
    for code, name in NETWORK_NAMES.items():
        text = re.sub(rf"(?<![\w-]){code}(?![\w-])", name, text or "")
    return text


REPORT_MARKS = {"✔": "ok", "✖": "ko", "…": "wait", "⚠": "ko"}
REPORT_KINDS = (("Rapport quotidien", "quotidien"), ("Indicateurs de la semaine", "hebdo"),
                ("Revue mensuelle", "mensuel"), ("Plan de la semaine", "plan"))


def report_blocks(text: str) -> list[tuple[str, str]]:
    """Texte brut d'un rapport → blocs à afficher : titre (« h »), puce (« li », « ok »,
    « ko », « wait » selon ✔ ✖ …) ou paragraphe (« p »). Rien n'est interprété en HTML."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r", "").split("\n")]
    out: list[tuple[str, str]] = []
    for n, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+", s)
        body = s[bullet.end():] if bullet else s
        mark = REPORT_MARKS.get(body[:1])
        if mark:
            out.append((mark, body[1:].strip()))
        elif bullet:
            out.append(("li", body))
        elif s.startswith("#"):
            out.append(("h", s.lstrip("# ").strip("* ")))
        else:
            nxt = next((x.strip() for x in lines[n + 1:] if x.strip()), "")
            listy = bool(re.match(r"^(?:[-*•]|\d+[.)])\s+|^[✔✖…⚠]", nxt))
            heading = len(s) <= 70 and (listy or s.endswith(":")) and not s.endswith(".")
            out.append(("h" if heading else "p", s.rstrip(":").strip("* ") if heading else s))
    return out


def report_kind(summary: str) -> str:
    return next((k for prefix, k in REPORT_KINDS if (summary or "").startswith(prefix)),
                "autre")


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

    def jour_titre(dt: datetime | None) -> str:
        """Titre de journée : « Aujourd'hui », « Hier » ou « mardi 29 sept. »."""
        if not dt:
            return ""
        d, today = local(dt).date(), datetime.now(tz).date()
        if d == today:
            return "Aujourd'hui"
        if d == today - timedelta(days=1):
            return "Hier"
        return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"

    def heure(dt: datetime | None) -> str:
        return f"{local(dt):%H:%M}" if dt else ""

    def jour_iso(value: str) -> str:
        """« 2026-10-06 » → « mardi 6 oct. »"""
        try:
            d = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
        return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"

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
                       initiales=initiales, jour_iso=jour_iso, jour_titre=jour_titre, heure=heure,
                       action_label=lambda t: ACTION_LABELS.get(t, (t or "").replace(".", " ")),
                       action_icon=lambda t: ACTION_ICONS.get((t or "").split(".")[0], "sparkles"),
                       heure_ts=lambda ts: heure(datetime.fromtimestamp(ts, UTC)),
                       lisible=lisible, rapport=report_blocks, genre_rapport=report_kind,
                       jours_restants=lambda v: (local(v).date() - datetime.now(tz).date()).days,
                       famille=lambda t: FAMILIES.get((t or "").split(".")[0], "Autres"))
    env.globals["aujourdhui"] = lambda: (
        f"{JOURS[datetime.now(tz).weekday()]} {datetime.now(tz).day} "
        f"{MOIS[datetime.now(tz).month - 1].rstrip('.')} {datetime.now(tz).year}")
SESSION_COOKIE = "ibig_session"
# Sources autorisées : le site lui-même, les polices Google ; scripts du site (en ligne).
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; "
       "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'; "
       "object-src 'none'")
RETURN_PAGES = ("/agents", "/rapports", "/objectifs", "/indicateurs", "/boites")
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

    https = rt.settings.dashboard_url.startswith("https")

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # Formulaires : refusés s'ils viennent d'un autre site (y compris un autre
        # sous-domaine du même domaine, que le cookie SameSite ne suffit pas à écarter).
        # API SARA, webhooks et liens de validation ont leur propre secret.
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(
                ("/api/", "/webhooks/", "/v/")):
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
                return PlainTextResponse("Requête refusée : elle ne vient pas du tableau de "
                                         "bord.", status_code=403)
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Content-Security-Policy", CSP)
        if https:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(NotLoggedIn)
    async def _to_login(request: Request, exc: NotLoggedIn):
        return RedirectResponse("/login", status_code=303)

    def user(request: Request) -> Principal:
        found = read_session_full(secret, request.cookies.get(SESSION_COOKIE, ""))
        principal = users.get(found[0], issued_at=found[1]) if found else None
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
            if len(failed_logins) > 5000:  # adresses inventées en masse : mémoire bornée
                for k in [k for k, v in failed_logins.items() if now - v[-1] > LOCKOUT_SECONDS]:
                    failed_logins.pop(k, None)
            return back("/login", "Identifiants invalides")
        failed_logins.pop(key, None)
        return with_session(RedirectResponse("/", status_code=303), principal.id)

    def with_session(resp, user_id: int):
        resp.set_cookie(SESSION_COOKIE, make_session(secret, user_id), httponly=True,
                        samesite="strict", secure=https, max_age=12 * 3600)
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
    def agents(request: Request, jour: str = "", who: Principal = Depends(user)):
        accounts = [(i, a) for i, a in enumerate(rt.org.social_accounts)
                    if a.pole in rt.org.pole_codes]
        sites = [(i, st) for i, st in enumerate(rt.org.sites)
                 if rt.contenus_web.why_skipped(st) is None]
        return page(request, "agents.html", cards=agent_cards(rt), accounts=accounts,
                    running=running, can_run=who.role != "valideur", sites=sites,
                    products=rt.kb.products(), jour=jour[:10])

    @app.post("/agents/contenu-web")
    def write_web_content(site: int = Form(...), genre: str = Form("article"),
                          sujet: str = Form(""), who: Principal = Depends(user)):
        if who.role == "valideur":
            return back("/agents", "Refusé : réservé à la direction")
        sites = rt.org.sites
        if not 0 <= site < len(sites) or rt.llm is None:
            return back("/agents", "Refusé : choisissez un site")
        target, sujet = sites[site], sujet.strip()[:300]
        reason = rt.contenus_web.why_skipped(target)
        if reason:
            return back("/agents", f"Refusé : {target.nom} — {reason}")
        if genre == "page" and not sujet:
            return back("/agents", "Refusé : indiquez le produit de la page")
        try:
            if genre == "page":
                rt.contenus_web.write_product_page(target, sujet)
            else:
                rt.contenus_web.write_article(target, sujet)
        except Exception as exc:  # noqa: BLE001 — affiché, rien n'est publié
            return back("/agents", f"Échec de la rédaction : {exc}"[:300])
        return back("/validations", "Brouillon rédigé : relisez-le ci-dessous puis validez")

    @app.post("/agents/lancer/{key}")
    def run_agent(key: str, tasks: BackgroundTasks, retour: str = Form(""),
                  who: Principal = Depends(user)):
        jobs = runners(rt)
        if key not in jobs:
            raise HTTPException(404)
        # Retour sur la page d'où le bouton a été cliqué (liste fermée : pas de redirection libre)
        dest = retour if retour in RETURN_PAGES else "/agents"
        if who.role == "valideur":
            return back(dest, "Refusé : les agents se lancent depuis un compte direction")
        if key in running:
            return back(dest, "Déjà en cours : patientez quelques instants")
        fn, msg = jobs[key]

        def job():
            running.add(key)
            try:
                _safe(f"{key} (tableau de bord, {who.email})", fn)()
            finally:
                running.discard(key)

        tasks.add_task(job)
        return back(dest, msg)

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
    social_routes.register(app, rt, user, page, back)

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
        with rt.sessions() as s:
            photos = {pid: aid for pid, aid in s.execute(
                select(MediaAsset.pending_id, MediaAsset.id).where(
                    MediaAsset.pending_id.in_([pa.id for pa in pending]))).all()}
        return page(request, "validations.html", pending=pending, prepared=prepared,
                    editable=EDITABLE_KEYS, photos=photos,
                    images_on=bool(service_value(rt.sessions, rt.settings, "openai_api_key")))

    @app.post("/validations/{pid}/photo")
    def make_photo(pid: int, who: Principal = Depends(user)):
        from ..images import generate_photo

        pa = load(pid)
        if (not who.can_decide(pa) or pa.status != "pending"
                or not pa.action_type.startswith("social.")):
            return back("/validations", "Refusé")
        pole = rt.org.pole(pa.pole)
        try:
            generate_photo(rt.sessions, rt.settings, pa, pole.activite if pole else "",
                           who.label, rt.image_factory)
        except Exception as exc:  # noqa: BLE001 — affiché au valideur
            return back("/validations", f"Photo non générée : {exc}"[:300])
        return back(f"/validations#p{pid}", "Photo générée : vérifiez le visuel avant de valider")

    @app.post("/validations/{pid}/photo/retirer")
    def remove_photo(pid: int, who: Principal = Depends(user)):
        pa = load(pid)
        if not who.can_decide(pa):
            return back("/validations", "Refusé")
        with rt.sessions() as s:
            for a in s.scalars(select(MediaAsset).where(MediaAsset.pending_id == pid)).all():
                s.delete(a)
            s.commit()
        return back(f"/validations#p{pid}", "Photo retirée : visuel aux couleurs IBIG seul")

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

    # ------------------------------------------------------------ validation par mail
    def link_target(token: str) -> tuple[PendingAction | None, Principal | None, str]:
        found = read_action_token(secret, token)
        if found is None:
            return None, None, "Lien invalide ou expiré (48 h) : validez depuis le tableau de bord."
        pid, uid = found
        who = users.get(uid)
        with rt.sessions() as s:
            pa = s.get(PendingAction, pid)
        if who is None or pa is None or not who.can_decide(pa) or pa.level != 2:
            return None, None, "Ce lien ne vous permet pas de décider de cet élément."
        return pa, who, ""

    @app.get("/v/{token}", response_class=HTMLResponse)
    def link_page(request: Request, token: str):
        pa, who, error = link_target(token)
        return page(request, "lien_validation.html", pa=pa, who=who, error=error,
                    token=token, done="")

    @app.post("/v/{token}", response_class=HTMLResponse)
    def link_decide(request: Request, token: str, decision: str = Form(...),
                    motif: str = Form("")):
        pa, who, error = link_target(token)
        done = ""
        if pa is not None:
            by = f"{who.label} (lien mail)"
            try:
                if decision == "valider":
                    out = rt.governor.approve(pa.id, by=by)
                    done = ("Validé et exécuté." if out.status == "executed"
                            else f"Échec de l'exécution : {out.error}")
                elif decision == "rejeter":
                    rt.governor.reject(pa.id, by=by, reason=motif)
                    done = "Rejeté."
                else:
                    raise HTTPException(400)
            except GovernanceError as exc:
                error = f"Refusé : {exc}"
        return page(request, "lien_validation.html", pa=pa, who=who, error=error,
                    token=token, done=done)

    @app.get("/visuel/{pid}.png")
    def visual_png(pid: int, who: Principal = Depends(user)):
        from ..images import photo_for

        pa = load(pid)
        if not pa.action_type.startswith("social.") or (
                who.role == "valideur" and pa.pole not in who.poles):
            raise HTTPException(404)
        pole = rt.org.pole(pa.pole)
        png = render_png(pa.title.split(" · ")[-1], first_sentence(pa.payload.get("texte", "")),
                         pa.pole, pa.payload.get("reseau", ""),
                         (pole.activite.split(",")[0] if pole and pole.activite else ""),
                         photo=photo_for(rt.sessions, pid))
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=300"})

    @app.get("/visuel/{pid}.svg")
    def visual(pid: int, who: Principal = Depends(user)):
        pa = load(pid)
        if not pa.action_type.startswith("social.") or (
                who.role == "valideur" and pa.pole not in who.poles):
            raise HTTPException(404)
        pole = rt.org.pole(pa.pole)
        svg = render_svg(pa.title.split(" · ")[-1], first_sentence(pa.payload.get("texte", "")),
                         pa.pole, pa.payload.get("reseau", ""),
                         (pole.activite.split(",")[0] if pole and pole.activite else ""))
        return Response(svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "private, max-age=300"})

    @app.get("/journal", response_class=HTMLResponse)
    def journal(request: Request, limit: int = 200, who: Principal = Depends(user)):
        q = select(JournalEntry).order_by(desc(JournalEntry.id)).limit(min(limit, 1000))
        if who.role == "valideur":  # entrées de ses pôles seulement
            q = q.where(JournalEntry.pole.in_(who.poles))
        with rt.sessions() as s:
            rows = s.scalars(q).all()
        return page(request, "journal.html", rows=rows,
                    agents=sorted({r.agent for r in rows if r.agent}))

    @app.get("/journal.csv")
    def journal_csv(who: Principal = Depends(user)):
        """Export du journal (audit, section 12) : 5 000 dernières entrées de ses pôles."""
        import csv
        import io

        q = select(JournalEntry).order_by(desc(JournalEntry.id)).limit(5000)
        if who.role == "valideur":
            q = q.where(JournalEntry.pole.in_(who.poles))
        with rt.sessions() as s:
            rows = s.scalars(q).all()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["date", "agent", "action", "niveau", "canal", "compte", "pole", "statut",
                    "resume", "decide_par"])
        for r in rows:
            w.writerow([_aware(r.created_at).isoformat(), r.agent, r.action_type, r.level,
                        r.channel, r.account, r.pole, r.status,
                        # Pas de formule exécutable à l'ouverture dans un tableur
                        ("'" + r.summary) if (r.summary or "")[:1] in "=+-@" else r.summary,
                        r.decided_by])
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=journal-ibig.csv"})

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
            cq = select(Ticket.status, func.count()).group_by(Ticket.status)
            if who.role == "valideur":
                cq = cq.where(Ticket.pole.in_(who.poles))
            counts = dict(s.execute(cq).all())
        if who.role == "valideur":
            rows = [t for t in rows if t.pole in who.poles]
        return page(request, "tickets.html", rows=rows, tous=tous,
                    n_open=counts.get("ouvert", 0), n_done=counts.get("resolu", 0))

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
        # Un numéro de valideur enregistré décide (OK 123 / NON 123) ; les autres
        # suivent le parcours client.
        agent, validation = rt.whatsapp, rt.wa_validation

        def route(m):
            if not validation.handle(m):
                agent.handle(m)

        for m in messages:
            tasks.add_task(route, m)
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
        kinds: dict[str, int] = {}
        for r in rows:
            kinds[report_kind(r.summary)] = kinds.get(report_kind(r.summary), 0) + 1
        return page(request, "rapports.html", rows=rows, kinds=kinds, running=set(running))

    @app.get("/indicateurs", response_class=HTMLResponse)
    def indicators(request: Request, jours: int = 7, who: Principal = Depends(user)):
        jours = min(max(jours, 1), 90)
        with rt.sessions() as s:
            alerts = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "veille.alert").order_by(desc(JournalEntry.id))
                .limit(30)).all()
        if who.role == "valideur":
            alerts = [a for a in alerts if not a.pole or a.pole in who.poles]
        from ..agents.veille import BASELINE_SETTING

        return page(request, "indicateurs.html", jours=jours,
                    report=rt.veille.indicators(jours), alerts=alerts,
                    baseline=service_value(rt.sessions, rt.settings, BASELINE_SETTING))

    @app.post("/indicateurs/reference")
    def set_baseline(heures: str = Form(""), who: Principal = Depends(user)):
        from ..agents.veille import BASELINE_SETTING
        from ..configstore import set_service_value

        if who.role == "valideur":
            raise HTTPException(403)
        try:
            value = float(heures.replace(",", ".")) if heures.strip() else 0.0
        except ValueError:
            return back("/indicateurs", "Refusé : indiquez un nombre d'heures")
        if not 0 <= value <= 1000:
            return back("/indicateurs", "Refusé : entre 0 et 1000 heures")
        set_service_value(rt.sessions, rt.settings, BASELINE_SETTING,
                          f"{value:g}" if value else "", who.label)
        return back("/indicateurs", "Référence enregistrée : le pourcentage d'heures "
                                    "économisées est calculé")

    @app.get("/utilisateurs", response_class=HTMLResponse)
    def accounts(request: Request, who: Principal = Depends(user)):
        if who.role != "admin":
            raise HTTPException(403)
        rows = [(u, rt.org.poles_of(u.email)) for u in users.all()]
        return page(request, "utilisateurs.html", rows=rows, poles=rt.org.poles,
                    whatsapp_ready=bool(rt.whatsapp_clients),
                    template=rt.settings.whatsapp_validation_template)

    @app.post("/utilisateurs/{uid}/mot-de-passe")
    def reset_password(uid: int, nouveau: str = Form(...), confirmation: str = Form(...),
                       who: Principal = Depends(user)):
        if who.role != "admin":
            raise HTTPException(403)
        if nouveau != confirmation:
            return back("/utilisateurs", "Refusé : les deux saisies ne correspondent pas")
        try:
            email = users.reset_password(uid, nouveau)
        except ValueError as exc:
            return back("/utilisateurs", f"Refusé : mot de passe {exc}")
        msg = f"Mot de passe de {email} changé : ses sessions ouvertes sont fermées"
        resp = back("/utilisateurs", msg)
        return with_session(resp, who.id) if uid == who.id else resp

    # ---------------------------------------------------------------- mon compte
    @app.get("/compte", response_class=HTMLResponse)
    def my_account(request: Request, who: Principal = Depends(user)):
        found = read_session_full(secret, request.cookies.get(SESSION_COOKIE, ""))
        with rt.sessions() as s:
            row = s.get(User, who.id)
        return page(request, "compte.html", me=row, min_password=MIN_PASSWORD,
                    session_end=(found[1] + SESSION_SECONDS) if found else 0)

    @app.post("/compte/mot-de-passe")
    def change_password(actuel: str = Form(...), nouveau: str = Form(...),
                        confirmation: str = Form(...), who: Principal = Depends(user)):
        if nouveau != confirmation:
            return back("/compte", "Refusé : les deux saisies du nouveau mot de passe "
                                   "ne correspondent pas")
        try:
            users.change_password(who.id, actuel, nouveau)
        except ValueError as exc:
            return back("/compte", f"Refusé : {exc}")
        # Nouvelle session sur cet appareil ; les autres appareils sont déconnectés.
        return with_session(back("/compte", "Mot de passe changé : vos autres appareils "
                                            "sont déconnectés"), who.id)

    @app.post("/compte/deconnecter-partout")
    def logout_everywhere(who: Principal = Depends(user)):
        users.revoke_sessions(who.id)
        return with_session(back("/compte", "Vos autres appareils sont déconnectés"), who.id)

    # ---------------------------------------------------------------- santé des tâches
    @app.get("/sante", response_class=HTMLResponse)
    def job_health(request: Request, who: Principal = Depends(user)):
        from ..scheduler import job_specs

        if who.role == "valideur":
            raise HTTPException(403)
        with rt.sessions() as s:
            rows = {r.job_id: r for r in s.scalars(select(JobStatus)).all()}
        sched = rt.scheduler
        jobs = []
        for spec in job_specs(rt):
            job = sched.get_job(spec.id) if sched is not None else None
            jobs.append((spec, rows.get(spec.id), getattr(job, "next_run_time", None)))
        # En échec d'abord, l'ordre habituel ensuite
        jobs.sort(key=lambda j: not (j[1] and j[1].failures))
        return page(request, "sante.html", jobs=jobs, scheduler_on=sched is not None,
                    running=set(running))

    @app.post("/sante/{job_id}/lancer")
    def run_job(job_id: str, tasks: BackgroundTasks, who: Principal = Depends(user)):
        from ..scheduler import job_specs, tracked

        if who.role == "valideur":
            raise HTTPException(403)
        spec = next((j for j in job_specs(rt) if j.id == job_id and j.manual), None)
        if spec is None:
            raise HTTPException(404)
        if f"job:{job_id}" in running:
            return back("/sante", "Déjà en cours : patientez quelques instants")

        def job():
            running.add(f"job:{job_id}")
            try:
                tracked(rt, spec.id, spec.name, spec.fn)()
            finally:
                running.discard(f"job:{job_id}")

        tasks.add_task(job)
        return back("/sante", f"« {spec.name} » lancée : actualisez dans quelques instants")

    @app.post("/utilisateurs/{uid}/telephone")
    def set_phone(uid: int, telephone: str = Form(""), who: Principal = Depends(user)):
        if who.role != "admin":
            raise HTTPException(403)
        try:
            digits = users.set_phone(uid, telephone)
        except ValueError as exc:
            return back("/utilisateurs", f"Refusé : {exc}")
        return back("/utilisateurs", f"Numéro WhatsApp enregistré : +{digits}" if digits
                    else "Numéro WhatsApp retiré")

    return app
