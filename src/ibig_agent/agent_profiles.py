"""Fiches de poste des agents (cahier des charges v2, section 7.1).

Les niveaux d'autonomie et les actions autorisées viennent de la gouvernance (une seule source de
vérité : `governance.AGENT_AUTONOMY` et `ACTION_LEVELS`). Ce module ajoute le reste de la fiche :
mission, compétences, connaissances, limites, qualité et escalade. Le responsable humain est
choisi dans l'application (page Supervision).
"""

from __future__ import annotations

from dataclasses import dataclass

from .governance import ACTION_LEVELS, AGENT_AUTONOMY, Level

AUTONOMY_COLORS = {"aucune": "rouge", "lecture": "rouge", "validation": "orange",
                   "automatique": "vert"}


@dataclass(frozen=True)
class AgentProfile:
    agent: str
    mission: str
    skills: tuple[str, ...]
    knowledge: str
    forbidden: tuple[str, ...]
    quality: str
    escalation: str
    actions: tuple[str, ...] = ()       # types d'actions que l'agent soumet à la gouvernance


COMMON_FORBIDDEN = ("Modifier son propre niveau d'autorisation", "S'approuver lui-même",
                    "Payer, rembourser, signer ou changer un tarif")

PROFILES: dict[str, AgentProfile] = {p.agent: p for p in (
    AgentProfile(
        "chef", "Coordonner, rendre compte à la direction et alerter.",
        ("Rapport quotidien, hebdomadaire et mensuel", "Plan de la semaine",
         "Découpage d'une instruction en tâches (Autopilot)"),
        "Journal, indicateurs, base de connaissances",
        (*COMMON_FORBIDDEN, "Écrire à un client ou publier"),
        "Chiffres tirés des journaux, jamais estimés",
        "Toute décision sensible est remontée à la direction",
        ("report.publish", "notify.internal", "stats.read")),
    AgentProfile(
        "messagerie", "Lire les boîtes mail et WhatsApp, trier, préparer les réponses.",
        ("Tri par pôle, catégorie et urgence", "Accusés de réception",
         "Réponses aux questions de la FAQ validée", "Brouillons de réponse",
         "Détection des mails suspects"),
        "FAQ validées et catalogue approuvé",
        (*COMMON_FORBIDDEN, "Répondre à un mail suspect", "Inventer un prix ou une promesse"),
        "Prix vérifiés contre le catalogue ; opposition (STOP) respectée",
        "Plaintes graves, litiges et mails suspects : dossier réservé à un humain",
        ("mail.label", "mail.ack", "mail.faq_reply", "mail.reply", "whatsapp.ack",
         "whatsapp.reply", "mail.forward_internal", "security.suspicious_message")),
    AgentProfile(
        "support", "Répondre aux questions d'usage des clients (SARA).",
        ("Réponses citant un guide approuvé", "Ouverture de tickets quand le guide manque"),
        "Guides marqués « réponses automatiques »",
        (*COMMON_FORBIDDEN, "Répondre sans source vérifiée"),
        "Chaque réponse cite un passage ; sinon brouillon ou escalade",
        "Guide absent ou sujet sensible : ticket pour un humain",
        ("support.answer", "sara.answer", "whatsapp.support_answer")),
    AgentProfile(
        "communication", "Préparer publications, calendrier éditorial et campagnes.",
        ("Publications pour les réseaux sociaux", "Calendrier éditorial",
         "Campagnes e-mail et WhatsApp"),
        "Charte, catalogue approuvé, publications réussies",
        (*COMMON_FORBIDDEN, "Publier ou envoyer sans validation humaine"),
        "Aucune affirmation produit non confirmée",
        "Sujet sensible : validation de la direction",
        ("social.post", "social.schedule_approved", "campaign.mail", "campaign.whatsapp")),
    AgentProfile(
        "contenus_web", "Rédiger articles et pages pour les sites du groupe.",
        ("Brouillons d'articles", "Pages produit"),
        "Catalogue approuvé et questions réelles des clients",
        (*COMMON_FORBIDDEN, "Publier sur un site sans validation"),
        "Prix et caractéristiques repris du catalogue",
        "Information incertaine : marquée « À COMPLÉTER »",
        ("web.article_draft",)),
    AgentProfile(
        "commercial", "Qualifier les prospects et suivre les relances.",
        ("Score et température d'un prospect", "Relances aux délais prévus",
         "Passage de main à un humain"),
        "Catalogue, fiches prospects, règles d'opposition",
        (*COMMON_FORBIDDEN, "Relancer un prospect qui a dit STOP", "Promettre une remise"),
        "Score expliqué ; relances plafonnées",
        "Prospect chaud ou demande de devis : remis à un commercial",
        ("commercial.followup",)),
    AgentProfile(
        "veille", "Surveiller les retards et les signaux faibles.",
        ("Alertes mails sans réponse", "Alertes mécontentement"),
        "Journal et messages traités",
        (*COMMON_FORBIDDEN, "Écrire à un client"),
        "Alertes sans doublon, une par situation",
        "Toute alerte va à l'équipe concernée",
        ("notify.internal",)),
    AgentProfile(
        "prospecteur", "Constituer des fiches prospects à partir de sources autorisées.",
        ("Lecture de fichiers déposés et de pages publiques autorisées",
         "Déduplication", "Score expliqué"),
        "Fichiers déposés sur la mission, robots.txt des sites",
        (*COMMON_FORBIDDEN, "Contacter un prospect", "Contourner une règle de plateforme"),
        "Chaque fiche garde sa source, sa date et ses raisons de score",
        "Source ambiguë : mission suspendue en attente de la direction",
        ()),
)}


def autonomy_of(agent: str) -> tuple[str, str]:
    """(niveau de la gouvernance, couleur de la v2) ; « aucune » si l'agent n'est pas déclaré."""
    level = AGENT_AUTONOMY.get(agent, "aucune")
    return level, AUTONOMY_COLORS.get(level, "rouge")


def action_levels(profile: AgentProfile) -> list[tuple[str, str]]:
    """Actions de la fiche avec leur niveau (1 automatique, 2 validation, 3 humain)."""
    names = {Level.AUTOMATIQUE: "automatique", Level.VALIDATION: "à valider",
             Level.HUMAIN: "réservée à un humain"}
    return [(a, names.get(ACTION_LEVELS.get(a, Level.HUMAIN), "réservée à un humain"))
            for a in profile.actions]
