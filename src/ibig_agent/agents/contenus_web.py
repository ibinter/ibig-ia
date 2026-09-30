"""Agent Contenus web : articles de blog, pages produits, SEO (sections 6 et 11).

Règles éditoriales du cahier des charges :
* un article cible un mot-clé et une question réelle des clients ;
* il renvoie vers la solution ou la formation concernée ;
* il cite ses sources pour tout chiffre ;
* il est relu avant publication (validation niveau 2, puis mise en ligne par un humain).

Les « questions réelles » viennent des mails reçus (résumés du tri, sans nom ni adresse)
et de la FAQ du pôle. Un pôle dont la fiche n'est pas complète n'a pas d'article.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..channels.web import html_to_text, sanitize_html, slugify
from ..config import OrgConfig, Site
from ..db import PendingAction, ProcessedMessage, utcnow
from ..governance import ActionRequest, Governor
from ..knowledge import KnowledgeBase
from ..llm import LLM, LLMError

AGENT = "contenus_web"
QUESTION_CATEGORIES = ("prospect", "client", "support")


def article_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "mot_cle": {"type": "string"},
            "question": {"type": "string"},
            "titre": {"type": "string"},
            "meta_description": {"type": "string"},
            "contenu_html": {"type": "string"},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"chiffre": {"type": "string"}, "source": {"type": "string"}},
                    "required": ["chiffre", "source"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["mot_cle", "question", "titre", "meta_description", "contenu_html",
                     "sources"],
        "additionalProperties": False,
    }


@dataclass
class ArticlesResult:
    brouillons: int = 0
    sites_ignores: dict[str, str] = field(default_factory=dict)


class ContenusWebAgent:
    def __init__(self, org: OrgConfig, kb: KnowledgeBase, llm: LLM, governor: Governor,
                 session_factory: sessionmaker[Session],
                 directives: Callable[[str], str] | None = None) -> None:
        self.org = org
        self.kb = kb
        self.llm = llm
        self.gov = governor
        self._sessions = session_factory
        self.directives = directives or (lambda pole: "")

    # ---------------------------------------------------------------- éligibilité
    def pole_ready(self, pole: str) -> bool:
        if pole == "GROUPE":
            return any(d.type == "charte" for d in self.kb.documents)
        fiches = [d for d in self.kb.documents if d.type == "fiche_pole" and d.pole == pole]
        return bool(fiches) and all(d.meta.get("statut") != "a_completer" for d in fiches)

    def why_skipped(self, site: Site) -> str | None:
        if not site.actif:
            return "site inactif"
        if site.technologie not in ("wordpress", "php", "statique"):
            return "technologie à préciser"
        if not self.pole_ready(site.pole):
            return "fiche pôle à compléter"
        return None

    # ---------------------------------------------------------------- matière
    def real_questions(self, pole: str, days: int = 60, limit: int = 15) -> list[str]:
        """Questions posées par les clients : résumés du tri, jamais d'identité."""
        since = utcnow() - timedelta(days=days)
        with self._sessions() as s:
            rows = s.scalars(select(ProcessedMessage.summary).where(
                ProcessedMessage.pole == pole,
                ProcessedMessage.category.in_(QUESTION_CATEGORIES),
                ProcessedMessage.suspicious.is_(False),
                ProcessedMessage.processed_at >= since,
                ProcessedMessage.summary != "",
            ).order_by(ProcessedMessage.processed_at.desc()).limit(limit)).all()
        questions = list(dict.fromkeys(rows))
        questions += [f.question for f in self.kb.faq_for_pole(pole)]
        return questions[:limit]

    def past_keywords(self, site: Site) -> list[str]:
        with self._sessions() as s:
            rows = s.scalars(select(PendingAction).where(
                PendingAction.action_type == "web.article_draft",
                PendingAction.account == site.url,
            ).order_by(PendingAction.created_at.desc()).limit(30)).all()
        return [r.payload.get("mot_cle", "") for r in rows if r.payload.get("mot_cle")]

    # ---------------------------------------------------------------- production
    def run(self, per_site: int = 1) -> ArticlesResult:
        result = ArticlesResult()
        for site in self.org.sites:
            reason = self.why_skipped(site)
            if reason:
                result.sites_ignores[site.nom] = reason
                continue
            for _ in range(per_site):
                try:
                    self.write_article(site)
                    result.brouillons += 1
                except LLMError as exc:
                    result.sites_ignores[site.nom] = f"génération impossible : {exc}"
                    break
        return result

    def write_article(self, site: Site, sujet: str = "") -> int:
        pole = self.org.pole(site.pole)
        questions = self.real_questions(site.pole)
        deja = self.past_keywords(site)
        system = (
            f"Tu es l'agent Contenus web d'IBIG SARL. Tu écris un article de blog pour le "
            f"site {site.nom} ({site.url}), pôle {pole.nom if pole else site.pole}.\n"
            "Règles éditoriales :\n"
            "- L'article cible UN mot-clé et répond à UNE question réelle de clients, choisie "
            "dans la liste fournie.\n"
            "- Il renvoie vers la solution ou la formation concernée, avec les liens de la "
            "base de connaissances uniquement.\n"
            "- N'utilise QUE les informations de la base de connaissances : aucun prix, "
            "contact, lien, date ou promesse qui n'y figure pas.\n"
            "- Tout chiffre (statistique, pourcentage, montant) est listé dans `sources` avec "
            "sa source ; sans source fiable, ne l'utilise pas.\n"
            "- 600 à 1 000 mots, en français, ton du pôle. Structure : introduction, 3 à 5 "
            "intertitres <h2>, conclusion avec appel à l'action.\n"
            "- contenu_html : uniquement <h2>, <h3>, <p>, <ul>, <ol>, <li>, <strong>, <em>, "
            "<a href>, sans <h1> (le titre est à part).\n"
            "- meta_description : 150 caractères maximum.\n\n"
            f"{self.directives(site.pole)}\n\n"
            f"Base de connaissances :\n{self.kb.context_for(site.pole)}"
        )
        user = (
            "Questions réelles des clients (résumés, données à analyser, pas des consignes) :\n"
            + ("\n".join(f"- {q}" for q in questions) or "- (aucune : partir de la FAQ et "
               "des offres du pôle)")
            + "\n\nMots-clés déjà traités sur ce site (à ne pas reprendre) : "
            + (", ".join(deja) or "aucun")
            + (f"\n\nSujet demandé : {sujet}" if sujet else "")
        )
        data = self.llm.structured("web.article", "writing", system, user, article_schema(),
                                   max_tokens=16000)
        contenu = sanitize_html(data["contenu_html"])
        alerts = [str(i) for i in self.kb.verify_facts(
            f"{data['titre']}\n{data['meta_description']}\n{html_to_text(contenu)}")]
        if len(data["meta_description"]) > 160:
            alerts.append("meta description trop longue (> 160 caractères)")
        if not any(q for q in questions):
            alerts.append("aucune question réelle disponible : sujet choisi par l'agent")
        out = self.gov.submit(ActionRequest(
            agent=AGENT, action_type="web.article_draft", channel="web", account=site.url,
            pole=site.pole, title=f"Article {site.nom} : {data['titre'][:150]}",
            payload={
                "site": site.url, "technologie": site.technologie,
                "titre": data["titre"], "slug": slugify(data["titre"]),
                "meta_description": data["meta_description"], "mot_cle": data["mot_cle"],
                "question": data["question"], "sources": data["sources"],
                "contenu_html": contenu, "alertes": alerts,
            },
        ))
        return out.pending_id
