"""Pages de configuration : objectifs de la direction, base de connaissances, boîtes mail.

Ce que le cahier des charges fait passer par le tableau de bord (section 5 : « les
humains n'interviennent qu'à un seul endroit ») plutôt que par des fichiers du serveur.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

import yaml
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select

from ..auth import Principal
from ..channels.emailing import BrevoClient, EmailingError
from ..config import Mailbox
from ..configstore import (
    reload_knowledge,
    service_value,
    set_service_value,
    sync_mailboxes,
    with_lws_defaults,
)
from ..db import (
    Directive,
    JournalEntry,
    KnowledgeEdit,
    MailboxAccount,
    PendingAction,
    utcnow,
)
from ..governance import as_utc
from ..knowledge import PLACEHOLDER, split_front_matter
from ..runtime import Runtime
from ..vault import encrypt

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KB_GROUPS = [
    ("fiche_pole", "Fiches des pôles", "book",
     "L'agent ne publie et ne répond qu'avec ce qui est écrit ici."),
    ("faq", "Questions fréquentes", "message",
     "Une réponse complétée part automatiquement quand un client pose la question."),
    ("guide", "Guides des solutions", "life",
     "Source des réponses du Support et de SARA."),
    ("publication", "Publications réussies", "sparkles",
     "Modèles de style pour l'agent Communication (menu « Publications réussies »)."),
    ("", "Charte, catalogues, contacts, interdits", "file",
     "Règles et informations communes à tout le groupe."),
]


COMMENT = re.compile(r"<!--.*?-->\s*", re.DOTALL)


def _todo_lines(text: str) -> list[str]:
    text = COMMENT.sub("", text)
    return [line.strip(" -#") for line in text.splitlines() if PLACEHOLDER.search(line)]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "guide"


def register(app: FastAPI, rt: Runtime, user, page, back) -> None:
    def direction_only(who: Principal) -> None:
        if who.role == "valideur":
            raise HTTPException(403)

    # ------------------------------------------------------------ objectifs
    @app.get("/objectifs", response_class=HTMLResponse)
    def objectives(request: Request, who: Principal = Depends(user)):
        with rt.sessions() as s:
            items = s.scalars(select(Directive).order_by(desc(Directive.created_at))
                              .limit(50)).all()
            plan = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "report.publish",
                JournalEntry.summary.like("Plan de la semaine%")).order_by(
                desc(JournalEntry.id)).limit(1)).first()
        now = utcnow()
        active = [d for d in items if d.active and (d.until is None or as_utc(d.until) >= now)]
        return page(request, "objectifs.html", active=active,
                    past=[d for d in items if d not in active], plan=plan,
                    poles=rt.org.poles, can_edit=who.role != "valideur")

    @app.post("/objectifs")
    def add_objective(texte: str = Form(...), pole: str = Form(""), jusqu_au: str = Form(""),
                      who: Principal = Depends(user)):
        direction_only(who)
        texte = texte.strip()[:1000]
        if not texte or (pole and pole not in rt.org.pole_codes):
            return back("/objectifs", "Refusé : écrivez un objectif")
        until = None
        if jusqu_au:
            until = datetime.combine(date.fromisoformat(jusqu_au), time(23, 59), UTC)
        with rt.sessions() as s:
            s.add(Directive(text=texte, pole=pole, until=until, created_by=who.label))
            s.commit()
        return back("/objectifs", "Objectif enregistré : les agents en tiennent compte "
                                  "dès leur prochaine rédaction")

    @app.post("/objectifs/{did}/retirer")
    def remove_objective(did: int, who: Principal = Depends(user)):
        direction_only(who)
        with rt.sessions() as s:
            d = s.get(Directive, did)
            if d is None:
                raise HTTPException(404)
            d.active = False
            s.commit()
        return back("/objectifs", "Objectif retiré")

    # ------------------------------------------------------------ calendrier éditorial
    @app.get("/calendrier", response_class=HTMLResponse)
    def calendar(request: Request, semaine: str = "", who: Principal = Depends(user)):
        today = datetime.now(UTC).date()
        try:
            start = date.fromisoformat(semaine) if semaine else today
        except ValueError:
            start = today
        start -= timedelta(days=start.weekday())
        days = [start + timedelta(days=i) for i in range(7)]
        with rt.sessions() as s:
            posts = s.scalars(select(PendingAction).where(
                PendingAction.action_type.in_(("social.post", "social.manual_post")),
                PendingAction.created_at >= datetime.combine(start - timedelta(days=45),
                                                             time(0), UTC))).all()
        by_day: dict[date, list] = {d: [] for d in days}
        for pa in posts:
            if who.role == "valideur" and pa.pole not in who.poles:
                continue
            try:
                day = date.fromisoformat(str(pa.payload.get("date", ""))[:10])
            except ValueError:
                continue
            if day in by_day:
                by_day[day].append(pa)
        counts = {st: sum(pa.status == st for lst in by_day.values() for pa in lst)
                  for st in ("pending", "executed", "rejected")}
        return page(request, "calendrier.html", days=days, by_day=by_day, counts=counts,
                    prev=(start - timedelta(days=7)).isoformat(),
                    next=(start + timedelta(days=7)).isoformat(), start=start, today=today)

    # ------------------------------------------------------------ base de connaissances
    @app.get("/connaissances", response_class=HTMLResponse)
    def knowledge(request: Request, who: Principal = Depends(user)):
        groups = []
        for kind, title, icon, why in KB_GROUPS:
            known = {k for k, *_ in KB_GROUPS if k}
            docs = [d for d in rt.kb.documents
                    if (d.type == kind if kind else d.type not in known)]
            rows = [(d, len(_todo_lines(d.body)), d.path in rt.kb.overrides) for d in docs]
            groups.append((title, icon, why, kind, rows))
        import json

        try:
            imports = json.loads(service_value(rt.sessions, rt.settings, "import_sites")
                                 or "{}")
        except ValueError:
            imports = {}
        return page(request, "connaissances.html", groups=groups,
                    can_edit=who.role != "valideur", poles=rt.org.poles,
                    faq_ready=len(rt.kb.faq), faq_todo=len(rt.kb.faq_pending),
                    imports=imports, importing=bool(_importing))

    _importing: set[str] = set()

    @app.post("/connaissances/importer")
    def import_sites(pole: str = Form(""), who: Principal = Depends(user)):
        import threading

        direction_only(who)
        if rt.llm is None:
            return back("/connaissances", "Refusé : l'IA n'est pas configurée")
        codes = [pole] if pole else [p.code for p in rt.org.poles if p.site]
        if any(c not in rt.org.pole_codes for c in codes):
            raise HTTPException(400)
        if _importing:
            return back("/connaissances", "Un import est déjà en cours : patientez")

        def job():
            _importing.add("x")
            try:
                rt.import_sites(codes)
            finally:
                _importing.clear()

        threading.Thread(target=job, daemon=True).start()
        return back("/connaissances", f"Lecture des sites lancée ({len(codes)} pôle(s)) : "
                                      "comptez 1 à 3 minutes par pôle, puis actualisez")

    def _doc_or_404(path: str):
        if ".." in path or not path.endswith(".md"):
            raise HTTPException(404)
        raw = rt.kb.raw(path)
        if not raw:
            raise HTTPException(404)
        return raw

    @app.get("/connaissances/document", response_class=HTMLResponse)
    def knowledge_doc(request: Request, chemin: str, who: Principal = Depends(user)):
        raw = _doc_or_404(chemin)
        meta, body = split_front_matter(raw)
        return page(request, "connaissance_edit.html", path=chemin, meta=meta,
                    body=COMMENT.sub("", body).strip(), todo=_todo_lines(body),
                    edited=chemin in rt.kb.overrides, can_edit=who.role != "valideur")

    @app.post("/connaissances/document")
    def knowledge_save(chemin: str = Form(...), contenu: str = Form(...),
                       valide: str = Form(""), reponses_auto: str = Form(""),
                       who: Principal = Depends(user)):
        direction_only(who)
        meta, _ = split_front_matter(_doc_or_404(chemin))
        contenu = contenu.replace("\r\n", "\n").strip() + "\n"
        todo = _todo_lines(contenu)
        if meta.get("type") == "fiche_pole":
            # Fiche utilisable par les agents seulement si validée ET sans « À COMPLÉTER »
            if valide and not todo:
                meta.pop("statut", None)
            else:
                meta["statut"] = "a_completer"
        if meta.get("type") == "guide":
            meta["reponses_auto"] = bool(reponses_auto)
        raw = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + contenu
        with rt.sessions() as s:
            s.merge(KnowledgeEdit(path=chemin, content=raw, updated_by=who.label,
                                  updated_at=utcnow()))
            s.commit()
        reload_knowledge(rt)
        msg = "Enregistré" + (f" : il reste {len(todo)} « À COMPLÉTER »" if todo else
                              " : document complet")
        return back(f"/connaissances/document?chemin={chemin}", msg)

    @app.post("/connaissances/restaurer")
    def knowledge_restore(chemin: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        with rt.sessions() as s:
            edit = s.get(KnowledgeEdit, chemin)
            if edit is not None:
                s.delete(edit)
                s.commit()
        reload_knowledge(rt)
        exists = bool(rt.kb.file_text(chemin))
        return back(f"/connaissances/document?chemin={chemin}" if exists else "/connaissances",
                    "Version d'origine rétablie" if exists else "Document supprimé")

    @app.post("/connaissances/guide")
    def knowledge_new_guide(titre: str = Form(...), pole: str = Form("SOFT"),
                            who: Principal = Depends(user)):
        direction_only(who)
        titre = titre.strip()[:150]
        if not titre or pole not in rt.org.pole_codes:
            return back("/connaissances", "Refusé : donnez un titre et un pôle")
        path = f"guides/{_slug(titre)}.md"
        if rt.kb.raw(path):
            return back("/connaissances", "Un guide porte déjà ce titre")
        meta = {"titre": titre, "pole": pole, "type": "guide", "reponses_auto": False}
        body = (f"# {titre}\n\n## Présentation\nÀ COMPLÉTER\n\n## Étapes\n1. À COMPLÉTER\n\n"
                "## Questions fréquentes\nÀ COMPLÉTER\n")
        raw = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + body
        with rt.sessions() as s:
            s.add(KnowledgeEdit(path=path, content=raw, updated_by=who.label))
            s.commit()
        reload_knowledge(rt)
        return back(f"/connaissances/document?chemin={path}", "Guide créé : rédigez-le")

    # ------------------------------------------------------------ publications réussies
    @app.get("/publications", response_class=HTMLResponse)
    def best_posts(request: Request, who: Principal = Depends(user)):
        posts = sorted((d for d in rt.kb.documents if d.type == "publication"),
                       key=lambda d: str(d.meta.get("ajoute_le", "")), reverse=True)
        networks = sorted({a.reseau for a in rt.org.social_accounts} | {"whatsapp_chaine"})
        return page(request, "publications.html", posts=posts, poles=rt.org.poles,
                    networks=networks, can_edit=who.role != "valideur")

    @app.post("/publications")
    def add_best_post(texte: str = Form(...), pole: str = Form("GROUPE"),
                      reseau: str = Form(""), resultats: str = Form(""),
                      who: Principal = Depends(user)):
        direction_only(who)
        texte = texte.strip()[:5000]
        if len(texte) < 20 or pole not in rt.org.pole_codes:
            return back("/publications", "Refusé : collez le texte de la publication")
        now = utcnow()
        path = f"publications/{now:%Y%m%d-%H%M%S}-{_slug(texte[:40])}.md"
        meta = {"titre": texte.splitlines()[0][:80], "pole": pole, "type": "publication",
                "reseau": reseau[:40], "resultats": resultats.strip()[:300],
                "ajoute_le": now.isoformat(timespec="seconds")}
        raw = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + texte + "\n"
        with rt.sessions() as s:
            s.add(KnowledgeEdit(path=path, content=raw, updated_by=who.label))
            s.commit()
        reload_knowledge(rt)
        return back("/publications", "Publication ajoutée : l'agent s'en inspire dès maintenant")

    @app.post("/publications/retirer")
    def remove_best_post(chemin: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        if not chemin.startswith("publications/"):
            raise HTTPException(404)
        with rt.sessions() as s:
            edit = s.get(KnowledgeEdit, chemin)
            if edit is not None:
                s.delete(edit)
                s.commit()
        reload_knowledge(rt)
        return back("/publications", "Publication retirée de la bibliothèque")

    # ------------------------------------------------------------ services extérieurs
    def brevo() -> BrevoClient:
        return rt.brevo_factory(service_value(rt.sessions, rt.settings, "brevo_api_key"))

    @app.get("/services", response_class=HTMLResponse)
    def services(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        get = lambda name: service_value(rt.sessions, rt.settings, name)
        sem = rt.semantic
        return page(request, "services.html", has_key=bool(get("brevo_api_key")),
                    sender_name=get("brevo_sender_name"),
                    sender_email=get("brevo_sender_email"),
                    voyage_key=bool(get("voyage_api_key")),
                    indexed=sem.count() if sem else 0,
                    passages=len([p for p in rt.kb.passages if p.doc.type != "publication"]),
                    pgvector=sem.pgvector() if sem else False,
                    sem_error=sem.last_error if sem else "",
                    openai_key=bool(get("openai_api_key")),
                    image_model=get("openai_image_model") or "gpt-image-1")

    @app.post("/services/voyage")
    def save_voyage(cle: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        if not cle.strip():
            return back("/services", "Refusé : collez la clé Voyage AI")
        set_service_value(rt.sessions, rt.settings, "voyage_api_key", cle.strip(), who.label,
                          secret=True)
        out = rt.index_knowledge()
        if out.get("erreur"):
            return back("/services", f"Clé enregistrée, mais l'indexation a échoué : "
                                     f"{out['erreur']}"[:300])
        return back("/services", f"Recherche par le sens activée : {out.get('total', 0)} "
                                 "passages indexés")

    @app.post("/services/voyage/indexer")
    def reindex(who: Principal = Depends(user)):
        direction_only(who)
        out = rt.index_knowledge()
        if out.get("erreur"):
            return back("/services", f"Échec de l'indexation : {out['erreur']}"[:300])
        return back("/services", f"Index à jour : {out.get('indexes', 0)} passage(s) "
                                 f"(ré)indexé(s), {out.get('total', 0)} au total")

    @app.post("/services/openai")
    def save_openai(cle: str = Form(""), modele: str = Form("gpt-image-1"),
                    who: Principal = Depends(user)):
        direction_only(who)
        if cle.strip():
            set_service_value(rt.sessions, rt.settings, "openai_api_key", cle.strip(),
                              who.label, secret=True)
        if modele not in ("gpt-image-1", "gpt-image-1-mini"):
            modele = "gpt-image-1"
        set_service_value(rt.sessions, rt.settings, "openai_image_model", modele, who.label)
        return back("/services", "Réglages des photos enregistrés : testez la clé")

    @app.post("/services/openai/tester")
    def test_openai(who: Principal = Depends(user)):
        from ..images import OpenAIImages

        direction_only(who)
        key = service_value(rt.sessions, rt.settings, "openai_api_key")
        model = service_value(rt.sessions, rt.settings, "openai_image_model") or "gpt-image-1"
        try:
            msg = (rt.image_factory or OpenAIImages)(key, model).check()
        except Exception as exc:  # noqa: BLE001 — affiché
            msg = f"Échec OpenAI : {exc}"
        return back("/services", msg[:300])

    @app.post("/services/brevo")
    def save_brevo(cle: str = Form(""), expediteur: str = Form(""),
                   expediteur_mail: str = Form(""), who: Principal = Depends(user)):
        direction_only(who)
        if expediteur_mail and not EMAIL.match(expediteur_mail.strip()):
            return back("/services", "Refusé : adresse d'expédition invalide")
        if cle.strip():
            set_service_value(rt.sessions, rt.settings, "brevo_api_key", cle.strip(),
                              who.label, secret=True)
        set_service_value(rt.sessions, rt.settings, "brevo_sender_name",
                          expediteur.strip()[:100], who.label)
        set_service_value(rt.sessions, rt.settings, "brevo_sender_email",
                          expediteur_mail.strip().lower(), who.label)
        return back("/services", "Réglages Brevo enregistrés : testez la connexion")

    @app.post("/services/brevo/tester")
    def test_brevo(who: Principal = Depends(user)):
        direction_only(who)
        try:
            client = brevo()
            lists = client.lists()
            msg = f"{client.check()} · {len(lists)} liste(s) de contacts"
        except (EmailingError, Exception) as exc:  # noqa: BLE001 — affiché
            msg = f"Échec Brevo : {exc}"[:300]
        return back("/services", msg)

    # ------------------------------------------------------------ campagnes
    @app.get("/campagnes", response_class=HTMLResponse)
    def campaigns(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        lists, error = [], ""
        if service_value(rt.sessions, rt.settings, "brevo_api_key"):
            try:
                lists = brevo().lists()
            except Exception as exc:  # noqa: BLE001 — affiché
                error = f"Brevo injoignable : {exc}"[:300]
        with rt.sessions() as s:
            history = s.scalars(select(PendingAction).where(
                PendingAction.action_type == "campaign.mail").order_by(
                desc(PendingAction.id)).limit(30)).all()
        return page(request, "campagnes.html", lists=lists, error=error, history=history,
                    poles=rt.org.poles,
                    configured=bool(service_value(rt.sessions, rt.settings, "brevo_api_key")),
                    sender_name=service_value(rt.sessions, rt.settings, "brevo_sender_name"),
                    sender_email=service_value(rt.sessions, rt.settings, "brevo_sender_email"))

    @app.post("/campagnes")
    def draft_campaign(pole: str = Form(...), sujet: str = Form(...), liste: str = Form(...),
                       who: Principal = Depends(user)):
        direction_only(who)
        sender = service_value(rt.sessions, rt.settings, "brevo_sender_name")
        sender_mail = service_value(rt.sessions, rt.settings, "brevo_sender_email")
        if pole not in rt.org.pole_codes or not sujet.strip() or not sender_mail:
            return back("/campagnes", "Refusé : pôle, sujet et expéditeur (menu Services) requis")
        try:
            list_id, _, list_name = liste.partition("|")
            pid = rt.campaigns.draft(pole, sujet.strip()[:300], int(list_id), list_name[:100],
                                     sender or "IBIG SARL", sender_mail)
        except Exception as exc:  # noqa: BLE001 — affiché, rien n'est envoyé
            return back("/campagnes", f"Échec de la rédaction : {exc}"[:300])
        if pid is None:
            return back("/campagnes", "Échec : campagne non créée")
        return back("/validations", "Campagne rédigée : relisez-la ci-dessous ; elle part "
                                    "dans Brevo dès votre validation")

    # ------------------------------------------------------------ boîtes mail
    @app.get("/boites", response_class=HTMLResponse)
    def mailboxes(request: Request, who: Principal = Depends(user)):
        direction_only(who)
        with rt.sessions() as s:
            stored = {r.adresse: r for r in s.scalars(select(MailboxAccount)).all()}
        boxes = [(m, m.adresse in rt.dashboard_boxes, stored.get(m.adresse))
                 for m in rt.org.mailboxes]
        return page(request, "boites.html", boxes=boxes, poles=rt.org.poles,
                    check=request.query_params.get("test", ""))

    @app.post("/boites")
    def add_mailbox(adresse: str = Form(...), hebergeur: str = Form("lws"),
                    pole: str = Form(...), secret: str = Form(...),
                    responsable: str = Form(""), signature: str = Form(""),
                    imap_host: str = Form(""), smtp_host: str = Form(""),
                    who: Principal = Depends(user)):
        direction_only(who)
        adresse = adresse.strip().lower()
        if not EMAIL.match(adresse) or hebergeur not in ("lws", "gmail") \
                or pole not in rt.org.pole_codes or not secret.strip():
            return back("/boites", "Refusé : adresse, hébergeur, pôle et mot de passe requis")
        if adresse in {m.adresse for m in rt.org.mailboxes} - rt.dashboard_boxes:
            return back("/boites", "Refusé : cette boîte est déjà réglée sur le serveur")
        box = with_lws_defaults(Mailbox(adresse=adresse, hebergeur=hebergeur, pole=pole,
                                        imap_host=imap_host.strip(),
                                        smtp_host=smtp_host.strip()))
        with rt.sessions() as s:
            s.merge(MailboxAccount(
                adresse=adresse, hebergeur=hebergeur, pole=pole,
                responsable=responsable.strip().lower(), signature=signature.strip(),
                imap_host=box.imap_host, smtp_host=box.smtp_host,
                secret_enc=encrypt(rt.settings.secret_key, secret.strip()),
                active=True, updated_by=who.label, updated_at=utcnow()))
            s.commit()
        sync_mailboxes(rt)
        return back("/boites", f"{adresse} raccordée : testez la connexion ci-dessous")

    @app.post("/boites/lot")
    def add_mailboxes(lignes: str = Form(...), pole: str = Form(...),
                      hebergeur: str = Form("lws"), who: Principal = Depends(user)):
        """Plusieurs boîtes d'un coup : une par ligne, « adresse ; mot de passe », avec
        facultativement « ; PÔLE ; responsable des transferts »."""
        direction_only(who)
        if pole not in rt.org.pole_codes or hebergeur not in ("lws", "gmail"):
            return back("/boites", "Refusé : choisissez un pôle")
        server = {m.adresse for m in rt.org.mailboxes} - rt.dashboard_boxes
        added, refused = [], []
        rows = [ln for ln in lignes.replace("\r", "").split("\n") if ln.strip()][:200]
        with rt.sessions() as s:
            for n, line in enumerate(rows, 1):
                sep = "\t" if "\t" in line else ";"
                parts = [p.strip() for p in line.split(sep)]
                adresse = parts[0].lower() if parts else ""
                secret = parts[1] if len(parts) > 1 else ""
                box_pole = parts[2].upper() if len(parts) > 2 and parts[2] else pole
                resp = parts[3].lower() if len(parts) > 3 else ""
                if (not EMAIL.match(adresse) or not secret or box_pole not in
                        rt.org.pole_codes or adresse in server
                        or (resp and not EMAIL.match(resp))):
                    refused.append(f"ligne {n}" + (f" ({adresse})" if EMAIL.match(adresse)
                                                   else ""))
                    continue  # le mot de passe n'est jamais réaffiché
                box = with_lws_defaults(Mailbox(adresse=adresse, hebergeur=hebergeur,
                                                pole=box_pole))
                s.merge(MailboxAccount(
                    adresse=adresse, hebergeur=hebergeur, pole=box_pole, responsable=resp,
                    imap_host=box.imap_host, smtp_host=box.smtp_host,
                    secret_enc=encrypt(rt.settings.secret_key, secret), active=True,
                    updated_by=who.label, updated_at=utcnow()))
                added.append(adresse)
            s.commit()
        sync_mailboxes(rt)
        msg = f"{len(added)} boîte(s) raccordée(s)"
        if refused:
            msg += f" ; refusé : {', '.join(refused[:10])} (format : adresse ; mot de passe)"
        return back("/boites", msg + (" — testez-les ci-dessous" if added else ""))

    @app.post("/boites/tester-tout")
    def test_all_mailboxes(who: Principal = Depends(user)):
        direction_only(who)
        ok, ko = 0, []
        for adresse, conn in list(rt.connectors.items()):
            if not hasattr(conn, "check"):
                continue
            try:
                conn.check()
                ok += 1
            except Exception:  # noqa: BLE001 — résumé affiché
                ko.append(adresse)
        return back("/boites", f"{ok} boîte(s) OK" + (
            f" ; en échec : {', '.join(ko[:15])}" if ko else ""))

    @app.post("/boites/tester")
    def test_mailbox(adresse: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        conn = rt.connectors.get(adresse)
        if conn is None or not hasattr(conn, "check"):
            return back("/boites", "Boîte introuvable")
        try:
            result = f"{adresse} : {conn.check()}"
        except Exception as exc:  # noqa: BLE001 — affiché à l'utilisateur
            result = f"Échec {adresse} : {type(exc).__name__} : {exc}"[:300]
        return back("/boites", result)

    @app.post("/boites/retirer")
    def remove_mailbox(adresse: str = Form(...), who: Principal = Depends(user)):
        direction_only(who)
        with rt.sessions() as s:
            row = s.get(MailboxAccount, adresse)
            if row is None:
                return back("/boites", "Seules les boîtes ajoutées ici se retirent ici")
            s.delete(row)
            s.commit()
        sync_mailboxes(rt)
        return back("/boites", f"{adresse} retirée : l'agent ne la lit plus")
