"""Page « Réseaux sociaux » : raccordement des comptes, publications programmées, veille
des commentaires (sections 6 et 8), et visuel public signé pour Meta."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import desc, select

from ..auth import Principal
from ..channels.social import FIELDS, PUBLISHERS, publisher_for
from ..db import ScheduledPost, SocialComment
from ..publishing import (
    forget_credentials,
    is_connected,
    load_credentials,
    read_media_token,
    save_credentials,
)
from ..runtime import Runtime
from .visuals import first_sentence, render_png

LABELS = {"facebook_page": "Page Facebook", "facebook_groupe": "Groupe Facebook",
          "instagram": "Instagram", "threads": "Threads", "linkedin": "LinkedIn", "x": "X",
          "tiktok": "TikTok", "whatsapp_chaine": "Chaîne WhatsApp"}
HOWTO = {
    "facebook_page": ("Meta Business Suite → Paramètres → Utilisateurs système : jeton de "
                      "page avec pages_manage_posts, pages_read_engagement, "
                      "pages_read_user_content (veille des commentaires)."),
    "instagram": ("Compte Instagram professionnel relié à une page Facebook ; jeton avec "
                  "instagram_basic et instagram_content_publish. Publie avec le visuel."),
    "threads": "Application Meta avec l'accès Threads : threads_basic, threads_content_publish.",
    "linkedin": ("Application LinkedIn avec « Community Management API » : jeton "
                 "w_organization_social, par un administrateur de la page."),
    "x": ("developer.x.com : application en lecture-écriture, puis les 4 clés "
          "(API Key, API Key Secret, Access Token, Access Token Secret)."),
}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    def account(idx: int):
        try:
            return rt.org.social_accounts[idx]
        except IndexError as exc:
            raise HTTPException(404) from exc

    @app.get("/reseaux", response_class=HTMLResponse)
    def networks(request: Request, who: Principal = Depends(user)):
        rows = []
        for i, a in enumerate(rt.org.social_accounts):
            if who.role == "valideur" and a.pole not in who.poles:
                continue
            auto = a.reseau in PUBLISHERS and a.publication_auto
            creds = load_credentials(rt.sessions, rt.settings, a.reseau, a.compte) if auto else {}
            rows.append({"i": i, "a": a, "label": LABELS.get(a.reseau, a.reseau), "auto": auto,
                         "connected": auto and is_connected(rt.sessions, rt.settings, a.reseau,
                                                            a.compte),
                         "fields": [(k, lab, bool(creds.get(k)))
                                    for k, lab in FIELDS.get(a.reseau, [])] if auto else [],
                         "howto": HOWTO.get(a.reseau, "")})
        with rt.sessions() as s:
            posts = s.scalars(select(ScheduledPost).order_by(desc(ScheduledPost.publish_at))
                              .limit(60)).all()
            comments = s.scalars(select(SocialComment).order_by(desc(SocialComment.seen_at))
                                 .limit(40)).all()
        if who.role == "valideur":
            posts = [p for p in posts if p.pole in who.poles]
            comments = [c for c in comments if c.pole in who.poles]
        return page(request, "reseaux.html", rows=rows, posts=posts, comments=comments,
                    labels=LABELS, hour=rt.settings.social_publish_hour,
                    public=rt.settings.dashboard_url.startswith("https://"),
                    can_edit=who.role != "valideur")

    @app.post("/reseaux/{idx}/acces")
    async def save_access(idx: int, request: Request, who: Principal = Depends(user)):
        direction_only(who)
        a = account(idx)
        if a.reseau not in PUBLISHERS:
            raise HTTPException(400)
        form = await request.form()
        save_credentials(rt.sessions, rt.settings, a.reseau, a.compte,
                         {k: str(form.get(k, ""))[:2000] for k, _ in FIELDS[a.reseau]},
                         who.label)
        return back("/reseaux", f"Accès enregistrés pour {a.compte} : testez la connexion")

    @app.post("/reseaux/{idx}/tester")
    def test_access(idx: int, who: Principal = Depends(user)):
        direction_only(who)
        a = account(idx)
        try:
            client = rt.social_client_factory() if rt.social_client_factory else None
            msg = publisher_for(a.reseau, load_credentials(rt.sessions, rt.settings, a.reseau,
                                                           a.compte),
                                rt.settings.whatsapp_api_version, client).check()
        except Exception as exc:  # noqa: BLE001 — affiché
            msg = f"Échec : {exc}"
        return back("/reseaux", f"{a.compte} — {msg}"[:300])

    @app.post("/reseaux/{idx}/retirer")
    def remove_access(idx: int, who: Principal = Depends(user)):
        direction_only(who)
        a = account(idx)
        forget_credentials(rt.sessions, a.reseau, a.compte)
        return back("/reseaux", f"Accès retirés pour {a.compte} : publication manuelle")

    def _post(sid: int, who: Principal) -> ScheduledPost:
        with rt.sessions() as s:
            p = s.get(ScheduledPost, sid)
        if p is None or (who.role == "valideur" and p.pole not in who.poles):
            raise HTTPException(404)
        return p

    @app.post("/reseaux/programme/{sid}/{action}")
    def post_action(sid: int, action: str, who: Principal = Depends(user)):
        _post(sid, who)
        allowed = {"annuler": ("programme", "annule"), "reessayer": ("echec", "programme")}
        if action not in allowed:
            raise HTTPException(400)
        before, after = allowed[action]
        with rt.sessions() as s:
            p = s.get(ScheduledPost, sid)
            if p.status != before:
                return back("/reseaux", "Refusé : état déjà changé")
            p.status = after
            s.commit()
        return back("/reseaux", "Publication annulée" if action == "annuler"
                    else "Nouvelle tentative dans les 5 minutes")

    # Visuel public signé : Meta télécharge l'image sans session (Instagram, Facebook).
    @app.get("/media/{token}.png")
    def media(token: str):
        sid = read_media_token(rt.settings.secret_key, token)
        if sid is None or len(rt.settings.secret_key) < 32:
            raise HTTPException(404)
        with rt.sessions() as s:
            p = s.get(ScheduledPost, sid)
        if p is None:
            raise HTTPException(404)
        pole = rt.org.pole(p.pole)
        png = render_png(p.titre or p.compte, first_sentence(p.texte), p.pole, p.reseau,
                         pole.activite.split(",")[0] if pole and pole.activite else "")
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
