"""Publication centrale d'articles (direction uniquement) : rédiger, valider, publier sur les sites, retirer."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import Principal
from ..runtime import Runtime

STATUS_LABEL = {"brouillon": "Brouillon", "pret": "Prêt à publier", "publie": "Publié"}
STATUS_TONE = {"brouillon": "amber", "pret": "green", "publie": "green"}


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    @app.get("/articles", response_class=HTMLResponse)
    def articles_page(request: Request, id: int = 0, who: Principal = Depends(user)):
        direction_only(who)
        found = rt.articles.get(id) if id else None
        return page(request, "articles.html", rows=rt.articles.rows(), targets=rt.articles.targets(),
                    article=found[0] if found else None, pubs=found[1] if found else [],
                    labels=STATUS_LABEL, tones=STATUS_TONE)

    @app.post("/articles")
    def article_create(titre: str = Form(""), contenu: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            aid = rt.articles.create(titre, contenu, who.email)
        except ValueError as exc:
            return back("/articles", str(exc))
        return RedirectResponse(f"/articles?id={aid}", status_code=303)

    @app.post("/articles/rediger")
    def article_draft(sujet: str = Form(""), points: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            aid = rt.articles.draft_with_ai(sujet, points, who.email)
        except ValueError as exc:
            return back("/articles", str(exc))
        except Exception as exc:  # noqa: BLE001 - panne de l'assistant : on explique sans planter la page
            return back("/articles", f"La rédaction a échoué ({type(exc).__name__}) : réessayez")
        return RedirectResponse(f"/articles?id={aid}", status_code=303)

    @app.post("/articles/{aid}")
    def article_save(aid: int, titre: str = Form(""), contenu: str = Form(""), resume: str = Form(""),
                     categorie: str = Form(""), image: str = Form(""), seo_titre: str = Form(""),
                     seo_description: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.articles.update(aid, titre, contenu, resume, categorie, image, seo_titre, seo_description)
        except ValueError as exc:
            return back(f"/articles?id={aid}", str(exc))
        return back(f"/articles?id={aid}", "Article enregistré")

    @app.post("/articles/{aid}/valider")
    def article_approve(aid: int, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.articles.approve(aid)
        except ValueError as exc:
            return back(f"/articles?id={aid}", str(exc))
        return back(f"/articles?id={aid}", "Article validé : il peut être publié")

    @app.post("/articles/{aid}/publier")
    async def article_publish(aid: int, request: Request, who: Principal = Depends(user)):
        direction_only(who)
        form = await request.form()
        targets = [str(t) for t in form.getlist("sites")]
        if not targets:
            return back(f"/articles?id={aid}", "Choisissez au moins un site")
        try:
            res = rt.articles.publish(aid, targets, who.email)
        except ValueError as exc:
            return back(f"/articles?id={aid}", str(exc))
        ok = sum(r["status"] in ("ok", "deja") for r in res)
        return back(f"/articles?id={aid}", f"Publié sur {ok} site(s) sur {len(res)}")

    @app.post("/articles/{aid}/retirer/{target}")
    def article_unpublish(aid: int, target: str, who: Principal = Depends(user)):
        direction_only(who)
        try:
            rt.articles.unpublish(aid, target, who.email)
        except ValueError as exc:
            return back(f"/articles?id={aid}", str(exc))
        return back(f"/articles?id={aid}", "Article retiré du site")
