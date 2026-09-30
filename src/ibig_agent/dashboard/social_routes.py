"""Page « Réseaux sociaux » : raccordement des comptes, publications programmées, veille
des commentaires (sections 6 et 8), et visuel public signé pour Meta."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import desc, select

from ..auth import Principal, normalize_phone
from ..channels.social import FIELDS, PUBLISHERS, publisher_for
from ..configstore import sync_social_accounts, sync_whatsapp
from ..db import ScheduledPost, SocialAccountRow, SocialComment, WhatsAppAccount, utcnow
from ..publishing import (
    forget_credentials,
    is_connected,
    load_credentials,
    read_media_token,
    save_credentials,
)
from ..runtime import Runtime
from ..vault import encrypt
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
        numbers = [(n, n.phone_number_id in rt.dashboard_numbers) for n in rt.org.whatsapp
                   if who.role != "valideur" or n.pole in who.poles]
        return page(request, "reseaux.html", rows=rows, posts=posts, comments=comments,
                    numbers=numbers, poles=rt.org.poles, mine=rt.dashboard_accounts,
                    labels=LABELS, hour=rt.settings.social_publish_hour,
                    public=rt.settings.dashboard_url.startswith("https://"),
                    can_edit=who.role != "valideur")

    @app.post("/reseaux/{idx:int}/acces")
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

    @app.post("/reseaux/{idx:int}/tester")
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

    @app.post("/reseaux/{idx:int}/retirer")
    def remove_access(idx: int, who: Principal = Depends(user)):
        direction_only(who)
        a = account(idx)
        forget_credentials(rt.sessions, a.reseau, a.compte)
        return back("/reseaux", f"Accès retirés pour {a.compte} : publication manuelle")

    # ------------------------------------------------------------ comptes et chaînes
    @app.post("/reseaux/comptes")
    def add_account(reseau: str = Form(...), compte: str = Form(...), pole: str = Form(...),
                    who: Principal = Depends(user)):
        direction_only(who)
        compte = " ".join(compte.split())[:200]
        if reseau not in LABELS or pole not in rt.org.pole_codes or not compte:
            return back("/reseaux", "Refusé : réseau, nom du compte et pôle requis")
        if any(a.reseau == reseau and a.compte == compte for a in rt.org.social_accounts):
            return back("/reseaux", "Ce compte existe déjà")
        with rt.sessions() as s:
            s.add(SocialAccountRow(reseau=reseau, compte=compte, pole=pole,
                                   publication_auto=reseau in PUBLISHERS,
                                   created_by=who.label))
            s.commit()
        sync_social_accounts(rt)
        return back("/reseaux", f"{LABELS[reseau]} « {compte} » ajouté"
                    + (" : raccordez ses accès ci-dessous" if reseau in PUBLISHERS
                       else " : publications préparées, à publier à la main"))

    @app.post("/reseaux/comptes/retirer")
    def remove_account(reseau: str = Form(...), compte: str = Form(...),
                       who: Principal = Depends(user)):
        direction_only(who)
        if (reseau, compte) not in rt.dashboard_accounts:
            return back("/reseaux", "Refusé : ce compte est déclaré sur le serveur")
        with rt.sessions() as s:
            row = s.scalars(select(SocialAccountRow).where(
                SocialAccountRow.reseau == reseau, SocialAccountRow.compte == compte)).first()
            if row is not None:
                s.delete(row)
                s.commit()
        forget_credentials(rt.sessions, reseau, compte)
        sync_social_accounts(rt)
        return back("/reseaux", f"« {compte} » retiré")

    # ------------------------------------------------------------ numéros WhatsApp
    @app.post("/reseaux/whatsapp")
    def add_number(nom: str = Form(...), numero: str = Form(...),
                   phone_number_id: str = Form(...), pole: str = Form(...),
                   jeton: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        pnid = phone_number_id.strip()
        digits = normalize_phone(numero)
        if (not pnid.isdigit() or not digits or pole not in rt.org.pole_codes
                or not nom.strip()):
            return back("/reseaux", "Refusé : nom, numéro, identifiant du numéro (chiffres) "
                                    "et pôle requis")
        if pnid in {n.phone_number_id for n in rt.org.whatsapp} - rt.dashboard_numbers:
            return back("/reseaux", "Refusé : ce numéro est réglé sur le serveur")
        with rt.sessions() as s:
            row = s.get(WhatsAppAccount, pnid)
            token = jeton.strip()
            enc = (encrypt(rt.settings.secret_key, token) if token
                   else (row.token_enc if row else ""))
            if not enc:
                return back("/reseaux", "Refusé : collez le jeton d'accès permanent")
            s.merge(WhatsAppAccount(phone_number_id=pnid, nom=nom.strip()[:200],
                                    numero=digits, pole=pole, token_enc=enc,
                                    updated_by=who.label, updated_at=utcnow()))
            s.commit()
        sync_whatsapp(rt)
        return back("/reseaux", f"Numéro +{digits} raccordé : testez-le")

    @app.post("/reseaux/whatsapp/tester")
    def test_number(phone_number_id: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        client = rt.whatsapp_clients.get(phone_number_id)
        if client is None:
            return back("/reseaux", "Numéro introuvable")
        try:
            msg = client.check()
        except Exception as exc:  # noqa: BLE001 — affiché
            msg = f"Échec : {exc}"
        return back("/reseaux", f"WhatsApp — {msg}"[:300])

    @app.post("/reseaux/whatsapp/retirer")
    def remove_number(phone_number_id: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        with rt.sessions() as s:
            row = s.get(WhatsAppAccount, phone_number_id)
            if row is None:
                return back("/reseaux", "Refusé : ce numéro est réglé sur le serveur")
            s.delete(row)
            s.commit()
        sync_whatsapp(rt)
        return back("/reseaux", "Numéro retiré")

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
        from ..images import photo_for

        pole = rt.org.pole(p.pole)
        png = render_png(p.titre or p.compte, first_sentence(p.texte), p.pole, p.reseau,
                         pole.activite.split(",")[0] if pole and pole.activite else "",
                         photo=photo_for(rt.sessions, p.pending_id) if p.pending_id else None)
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
