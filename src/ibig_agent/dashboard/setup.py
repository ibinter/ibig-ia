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
    link: str = ""  # page du tableau de bord où se fait l'étape


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
    from ..publishing import is_connected

    auto_on = sum(is_connected(rt.sessions, rt.settings, a.reseau, a.compte)
                  for a in org.social_accounts if a.publication_auto)
    from ..agents.veille import BASELINE_SETTING
    from ..configstore import service_value
    from ..db import User

    get = lambda name: service_value(rt.sessions, rt.settings, name)
    with rt.sessions() as s:
        phones = [u for u in s.query(User).filter(User.active.is_(True)).all() if u.phone]
    steps = [
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
             "Menu « Boîtes mail » : adresse, LWS ou Gmail, pôle et mot de passe (chiffré), "
             "puis « Tester la connexion ».",
             f"{len(rt.connectors)} boîte(s) raccordée(s)" if rt.connectors
             else "aucune boîte raccordée"),
        Step("fiches", "book", "Compléter les fiches des pôles", not fiches_todo,
             "L'agent ne répond qu'avec ce qui est écrit dans ces fiches : offres, prix, "
             "contacts, délais. Rien d'inventé.",
             "Menu « Base de connaissances » : ouvrir la fiche du pôle, remplacer chaque "
             "« À COMPLÉTER », cocher « Fiche validée » et enregistrer.",
             "complètes" if not fiches_todo else f"à compléter : {', '.join(fiches_todo)}"),
        Step("faq", "message", "Rédiger les réponses aux questions fréquentes", bool(kb.faq),
             "Les questions simples (horaires, tarifs, inscription) reçoivent alors une "
             "réponse immédiate, sans attendre personne.",
             "Menu « Base de connaissances » → Questions fréquentes (36 questions déjà "
             "préparées) : écrire les réponses.",
             f"{len(kb.faq)} réponse(s) prête(s)" if kb.faq
             else f"{len(kb.faq_pending)} réponse(s) à rédiger"),
        Step("canaux", "share", "Réseaux sociaux, sites et WhatsApp", bool(channels),
             "Publications préparées chaque semaine, articles de blog, messages WhatsApp.",
             "Plus tard, une fois les mails rodés : config/canaux.yaml, sites.yaml, "
             "whatsapp.yaml.",
             f"branchés : {', '.join(channels)}" if channels else "à brancher après les mails",
             optional=True),
        Step("publication", "share", "Publier automatiquement sur les réseaux", auto_on > 0,
             "Une publication validée part seule à sa date, avec son visuel ; les "
             "commentaires des pages Facebook sont surveillés.",
             "Menu « Réseaux sociaux » : pour chaque page ou compte, coller les accès "
             "fournis par Meta, LinkedIn ou X, puis « Tester la connexion ».",
             f"{auto_on} compte(s) en publication automatique" if auto_on
             else "publication manuelle pour l'instant", optional=True, link="/reseaux"),
        Step("reference", "clock", "Indiquer le temps passé avant l'agent",
             bool(get(BASELINE_SETTING)),
             "Pour mesurer l'objectif « −60 % d'heures de communication manuelle » "
             "(section 3).",
             "Menu « Indicateurs » : heures par semaine passées à trier les mails, répondre, "
             "rédiger les publications et les articles.",
             f"{get(BASELINE_SETTING)} h par semaine" if get(BASELINE_SETTING)
             else "non renseigné", optional=True, link="/indicateurs"),
        Step("whatsapp_validation", "message", "Valider depuis WhatsApp", bool(phones),
             "Les valideurs répondent « OK 123 » depuis leur téléphone, sans ouvrir le "
             "tableau de bord.",
             "Raccorder un numéro WhatsApp Business (Meta), puis menu « Comptes » : "
             "numéro WhatsApp de chaque valideur.",
             f"{len(phones)} valideur(s) avec un numéro" if phones else "aucun numéro",
             optional=True, link="/utilisateurs"),
        Step("recherche", "book", "Activer la recherche par le sens", bool(get("voyage_api_key")),
             "Le Support retrouve la bonne réponse même quand le client emploie d'autres "
             "mots que la base.",
             "Menu « Services » → Voyage AI : coller la clé (dash.voyageai.com).",
             "active" if get("voyage_api_key") else "mots-clés seuls", optional=True,
             link="/services"),
        Step("photos", "sparkles", "Activer les photos par IA", bool(get("openai_api_key")),
             "Une photo réaliste pour chaque publication, habillée aux couleurs IBIG.",
             "Menu « Services » → Photos par IA : coller la clé OpenAI.",
             "active" if get("openai_api_key") else "visuels aux couleurs IBIG seuls",
             optional=True, link="/services"),
        Step("brevo", "mail", "Envoyer des campagnes mail", bool(get("brevo_api_key")),
             "Newsletters et annonces aux contacts qui l'ont accepté, par un service "
             "d'emailing (section 9).",
             "Menu « Services » → Brevo : clé API, expéditeur, domaine authentifié.",
             "configuré" if get("brevo_api_key") else "non configuré", optional=True,
             link="/services"),
    ]
    links = {"mails": "/boites", "fiches": "/connaissances", "faq": "/connaissances",
             "canaux": "/reseaux"}
    for st in steps:
        st.link = st.link or links.get(st.key, "")
    return steps


def progress(steps: list[Step]) -> tuple[int, int]:
    required = [s for s in steps if not s.optional]
    return sum(s.done for s in required), len(required)
