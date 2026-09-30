"""Mise en route : ce qui reste à faire pour que l'agent travaille, en langage simple.

Calculé sans appel extérieur (pas de connexion aux boîtes ni à l'API) pour rester
instantané ; `ibig-agent diagnostic` fait les vérifications complètes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..runtime import Runtime


@dataclass
class Step:
    key: str
    icon: str
    title: str
    done: bool
    why: str
    how: str
    detail: str = ""
    optional: bool = False


def setup_steps(rt: Runtime) -> list[Step]:
    kb, org = rt.kb, rt.org
    poles = [p for p in org.poles if p.code != "GROUPE"]
    no_validator = [p.code for p in poles if not p.valideur]
    fiches_todo = sorted({d.pole for d in kb.documents
                          if d.type == "fiche_pole" and d.meta.get("statut") == "a_completer"})
    real_ai = type(rt.llm).__name__ == "ClaudeClient"
    ai_ok = not real_ai or bool(os.environ.get("ANTHROPIC_API_KEY")
                                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    channels = [label for label, on in (
        ("réseaux sociaux", org.social_accounts),
        ("sites", [s for s in org.sites if s.actif]),
        ("WhatsApp", org.whatsapp)) if on]
    return [
        Step("install", "shield-check", "Installer le tableau de bord", True,
             "C'est ici que vous suivez et validez tout ce que fait l'agent.",
             "Fait : vous êtes connecté.", "installé, en HTTPS"),
        Step("ia", "sparkles", "Brancher l'intelligence artificielle", ai_ok,
             "L'agent utilise Claude pour lire, classer et rédiger.",
             "Clé API Anthropic à mettre dans le fichier .env du serveur.",
             "connectée" if ai_ok else "clé API manquante"),
        Step("valideurs", "users", "Désigner qui valide", not no_validator,
             "Chaque réponse importante attend l'accord d'une personne avant de partir.",
             "Une adresse par pôle dans config/poles.yaml, ou une seule pour tous "
             "(IBIG_VALIDEUR_DEFAUT dans .env).",
             "un valideur par pôle" if not no_validator
             else f"sans valideur : {', '.join(no_validator)}"),
        Step("mails", "mail", "Raccorder les boîtes mail", bool(rt.connectors),
             "C'est la source du travail : l'agent lit les mails reçus, les classe et prépare "
             "les réponses. Sans boîte raccordée, il n'a rien à traiter — c'est pourquoi "
             "les compteurs sont à zéro.",
             "Donnez la liste des boîtes (adresse, LWS ou Gmail, pôle) à la personne qui "
             "installe l'agent. Les mots de passe se saisissent directement sur le serveur.",
             f"{len(rt.connectors)} boîte(s) raccordée(s)" if rt.connectors
             else "aucune boîte raccordée"),
        Step("fiches", "book", "Compléter les fiches des pôles", not fiches_todo,
             "L'agent ne répond qu'avec ce qui est écrit dans ces fiches : offres, prix, "
             "contacts, délais. Rien d'inventé.",
             "Remplacer chaque « À COMPLÉTER » dans knowledge/poles/ (une fiche par pôle).",
             "complètes" if not fiches_todo else f"à compléter : {', '.join(fiches_todo)}"),
        Step("faq", "message", "Rédiger les réponses aux questions fréquentes", bool(kb.faq),
             "Les questions simples (horaires, tarifs, inscription) reçoivent alors une "
             "réponse immédiate, sans attendre personne.",
             "Compléter les réponses dans knowledge/faq/ (36 questions déjà préparées).",
             f"{len(kb.faq)} réponse(s) prête(s)" if kb.faq
             else f"{len(kb.faq_pending)} réponse(s) à rédiger"),
        Step("canaux", "share", "Réseaux sociaux, sites et WhatsApp", bool(channels),
             "Publications préparées chaque semaine, articles de blog, messages WhatsApp.",
             "Plus tard, une fois les mails rodés : config/canaux.yaml, sites.yaml, "
             "whatsapp.yaml.",
             f"branchés : {', '.join(channels)}" if channels else "à brancher après les mails",
             optional=True),
    ]


def progress(steps: list[Step]) -> tuple[int, int]:
    required = [s for s in steps if not s.optional]
    return sum(s.done for s in required), len(required)
