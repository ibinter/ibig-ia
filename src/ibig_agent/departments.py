"""Départements du cahier des charges v2 (section 4) : fiche de poste, agents qui les servent et
procédure associée.

Règle 4.1 du cahier : on n'ajoute pas d'agent si les agents existants suffisent. Chaque département
est donc servi par des agents EXISTANTS (`agents`) et des procédures de la bibliothèque. Le
« niveau de service » dit honnêtement ce qui est automatisé :
  - agent      : un agent travaille de façon continue (tri, relances…), sous validation ;
  - procedure  : un plan de tâches se lance à la demande, les agents préparent des brouillons ;
  - manuel     : tout reste à faire par une personne, avec une fiche pour ne rien oublier.
Aucun département n'envoie, ne publie, ne paie ni ne signe sans validation humaine.
"""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = {"agent": "Un agent travaille en continu", "procedure": "Procédure + brouillons d'agents",
          "manuel": "Fiche et suivi, travail humain"}


@dataclass(frozen=True)
class Department:
    code: str
    name: str
    mission: str
    level: str
    agents: tuple[str, ...]
    procedures: tuple[str, ...]
    kpis: str
    limits: str
    escalation: str


DEPARTMENTS: tuple[Department, ...] = (
    Department(
        "COMMERCIAL", "Direction commerciale",
        "Prospection, qualification, relance, rendez-vous, suivi des devis et des ventes.",
        "agent", ("prospecteur", "commercial", "messagerie"),
        ("qualification-prospect", "preparation-devis", "relance-facture-echue"),
        "Prospects, conversion, ventes, chiffre d'affaires",
        "Aucun envoi sans validation ; prix uniquement du catalogue ; opposition STOP respectée",
        "Remise hors barème, litige ou gros compte : direction commerciale"),
    Department(
        "EDUFORM", "IBIG EDUFORM",
        "Leads formation, programmes, inscriptions, sessions, formateurs, suivi des apprenants.",
        "procedure", ("messagerie", "commercial", "communication"),
        ("preparation-session-formation",),
        "Inscriptions, remplissage, paiements confirmés",
        "Une place n'est confirmée qu'après validation et paiement",
        "Moins de participants que le minimum : décision de report par le pôle"),
    Department(
        "SOFT", "IBIG SOFT",
        "Qualification des besoins logiciels, démonstrations, support de premier niveau.",
        "agent", ("support", "commercial", "messagerie"),
        ("demande-demonstration-logiciel", "traitement-ticket"),
        "Démos, opportunités, tickets, délais",
        "Réponses appuyées sur un guide approuvé ; sinon ticket ouvert",
        "Besoin hors périmètre : autre branche du groupe"),
    Department(
        "DIGITAL", "IBIG DIGITAL",
        "Demandes web, graphisme, vidéo, automatisation : brief, devis préparatoires, suivi.",
        "procedure", ("commercial", "contenus_web", "communication"),
        ("projet-digital", "preparation-devis"),
        "Demandes, devis, projets livrés",
        "Aucun délai ni chiffre cité tant que le pôle n'en a pas validé un",
        "Demande hors devis : nouveau devis avant toute réalisation"),
    Department(
        "MARKETING", "Marketing et communication",
        "Calendrier éditorial, rédaction, visuels, campagnes et analyses.",
        "agent", ("communication", "contenus_web", "veille"),
        ("publication-contenu",),
        "Publications, leads, coût par lead",
        "Aucune publication sans validation ; aucune affirmation produit non confirmée",
        "Sujet sensible : validation de la direction"),
    Department(
        "RH", "RH et recrutement",
        "Réception des candidatures, classement, complétude, présélection assistée, suivi.",
        "procedure", ("messagerie",),
        ("traitement-candidature",),
        "Candidatures, délais de réponse, présélection",
        "Aucune décision automatique de refus ; présélection proposée, jamais décidée",
        "Toute décision sur un candidat : responsable RH"),
    Department(
        "ADMIN", "Administration",
        "Courriers, comptes rendus, classement, rappels, préparation des dossiers.",
        "manuel", ("messagerie", "chef"),
        ("rapport-quotidien",),
        "Dossiers complets, échéances tenues",
        "L'agent classe et rappelle ; il ne signe ni n'envoie de courrier officiel",
        "Courrier officiel ou juridique : direction"),
    Department(
        "FINANCE", "Finance et contrôle",
        "Factures, échéances, rapprochements préparatoires, alertes de trésorerie.",
        "procedure", ("commercial", "chef"),
        ("relance-facture-echue",),
        "Encaissements, impayés, échéances",
        "Aucun paiement ; confirmation d'encaissement par une personne (deux si possible)",
        "Litige, écart ou impayé important : direction"),
    Department(
        "PARTNERS", "IBIG PARTNERS",
        "Recrutement de partenaires, suivi des apporteurs, opportunités, commissions.",
        "procedure", ("communication", "commercial"),
        ("onboarding-partenaire",),
        "Partenaires actifs, leads, commissions",
        "Commissions inactives tant que la direction n'a pas confirmé les règles ; aucun gain promis",
        "Règles de commission et questions juridiques : direction"),
    Department(
        "IMMOTRUST", "IBIG IMMO TRUST",
        "Qualification des demandes, biens, prospects, visites et suivi des dossiers.",
        "procedure", ("messagerie", "commercial"),
        ("qualification-prospect", "preparation-devis"),
        "Demandes, visites, mandats",
        "Aucun financement ni garantie promis sans les conditions validées par le pôle",
        "Financement, garantie ou litige : responsable du pôle"),
    Department(
        "MULTISERVICES", "IBIG MULTISERVICES",
        "Réception, qualification, affectation et suivi des prestations.",
        "procedure", ("messagerie", "commercial"),
        ("qualification-prospect",),
        "Interventions, délais, satisfaction",
        "Besoins précis à compléter par le pôle",
        "Prestation hors catalogue : responsable du pôle"),
    Department(
        "TV", "IBIG TV et contenus",
        "Préparation de sujets, scripts, calendrier et suivi de production.",
        "procedure", ("communication", "contenus_web"),
        ("publication-contenu",),
        "Contenus préparés et publiés",
        "Scripts en brouillon ; aucune diffusion sans validation",
        "Sujet sensible : validation de la direction"),
    Department(
        "DIRECTION", "Direction générale",
        "Piloter, décider, valider les exceptions et lire le rapport quotidien.",
        "agent", ("chef",),
        ("rapport-quotidien",),
        "Rapport lu, validations traitées, incidents",
        "Le chef d'orchestre alerte et rend compte ; il ne décide pas à la place de la direction",
        "Tout est remonté à la direction"),
)

BY_CODE = {d.code: d for d in DEPARTMENTS}
