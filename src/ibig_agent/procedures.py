"""Bibliothèque de procédures (cahier des charges v2, section 7.3) : des modèles de plan versionnés.

Une procédure est un plan de tâches prêt à l'emploi (agent, dépendances, preuve attendue). L'Autopilot
la lance SANS appel d'IA : aucun coût, aucun crédit nécessaire. Elles sont versionnées ici (git) ;
chaque évolution augmente `version`. « Agent » vide = une personne. Aucune étape n'envoie, ne publie
ni ne facture sans une étape humaine de validation placée avant.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    title: str
    agent: str = ""                 # vide = une personne
    after: tuple[int, ...] = ()     # numéros (base 1) d'étapes précédentes
    proof: str = ""                 # ce qui prouve que l'étape est faite


@dataclass(frozen=True)
class Procedure:
    code: str
    title: str
    version: str
    trigger: str
    owner: str                      # fonction responsable
    result: str
    quality: str
    exceptions: str
    steps: tuple[Step, ...] = field(default_factory=tuple)


PROCEDURES: tuple[Procedure, ...] = (
    Procedure(
        "qualification-prospect", "Qualification d'un prospect", "1.0",
        "Un prospect est enregistré (import, mail, mission de prospection)",
        "Direction commerciale", "Prospect noté, avec besoin, branche et prochaine action",
        "Score expliqué ; aucune donnée inventée", "Prospect sans e-mail professionnel : "
        "recherche complémentaire avant contact",
        (Step("Vérifier la fiche : origine, doublon, adresse professionnelle", "commercial",
              proof="Fiche vérifiée ou doublon identifié"),
         Step("Identifier le besoin et la branche concernée", "commercial", (1,),
              "Besoin et branche notés sur la fiche"),
         Step("Valider le score et choisir la prochaine action", "", (2,),
              "Prochaine action inscrite au fil d'activités"))),
    Procedure(
        "traitement-candidature", "Traitement d'une candidature", "1.0",
        "Une candidature arrive par mail", "RH & recrutement",
        "Candidature classée, dossier vérifié, présélection proposée",
        "Dossier complet ; aucune décision automatique de refus",
        "Dossier incomplet : demander les pièces manquantes",
        (Step("Vérifier la complétude du dossier (CV, lettre, pièces)", "messagerie",
              proof="Liste des pièces présentes et manquantes"),
         Step("Rédiger le brouillon d'accusé de réception ou de demande de pièces",
              "messagerie", (1,), "Brouillon relu"),
         Step("Décider de la présélection", "", (1,), "Décision notée"),
         Step("Informer le candidat", "", (2, 3), "Réponse envoyée par une personne"))),
    Procedure(
        "preparation-session-formation", "Préparation d'une session de formation", "1.0",
        "Une session est programmée", "IBIG EDUFORM",
        "Session prête : inscrits, convocations, supports, formateur confirmé",
        "Tarif et dates conformes au catalogue", "Moins de participants que le minimum : "
        "décider du report",
        (Step("Vérifier les inscriptions et les paiements confirmés", "commercial",
              proof="Liste des inscrits payés"),
         Step("Confirmer le formateur et la salle", "", proof="Confirmation reçue"),
         Step("Préparer les convocations", "communication", (1, 2), "Brouillon relu"),
         Step("Envoyer les convocations", "", (3,), "Envoi confirmé"))),
    Procedure(
        "demande-demonstration-logiciel", "Réponse à une demande de démonstration", "1.0",
        "Un prospect demande une démonstration", "IBIG SOFT",
        "Démonstration planifiée et préparée",
        "Besoins du prospect relus avant la démo", "Besoin hors périmètre : orienter vers une "
        "autre branche",
        (Step("Résumer le besoin et le produit concerné", "commercial",
              proof="Résumé du besoin"),
         Step("Proposer deux créneaux", "", (1,), "Créneaux envoyés"),
         Step("Préparer le scénario de démonstration", "support", (1,), "Scénario relu"),
         Step("Tenir la démonstration et noter les suites", "", (2, 3),
              "Compte rendu enregistré"))),
    Procedure(
        "preparation-devis", "Préparation d'un devis", "1.0",
        "Un prospect accepte de recevoir une proposition", "Direction commerciale",
        "Devis brouillon prêt à valider", "Prix uniquement issus du catalogue approuvé ; "
        "remise hors barème interdite sans validation",
        "Offre à tarif à confirmer : saisir le prix à la main et le signaler",
        (Step("Choisir les offres du catalogue", "commercial", proof="Offres choisies"),
         Step("Créer le devis brouillon dans l'application", "", (1,), "Numéro du devis"),
         Step("Valider le devis", "", (2,), "Devis validé"),
         Step("Remettre le devis au client", "", (3,), "Envoi confirmé"))),
    Procedure(
        "relance-facture-echue", "Relance d'une facture échue", "1.0",
        "Une échéance est dépassée", "Finance & contrôle",
        "Client relancé, réponse ou paiement suivi", "Aucun montant inventé ; ton courtois",
        "Litige déclaré : suspendre la relance",
        (Step("Vérifier le montant dû et les paiements confirmés", "",
              proof="Montant et date d'échéance"),
         Step("Préparer le brouillon de relance", "commercial", (1,), "Brouillon relu"),
         Step("Envoyer la relance", "", (2,), "Envoi confirmé"),
         Step("Noter la réponse ou planifier la suivante", "", (3,), "Réponse notée"))),
    Procedure(
        "publication-contenu", "Publication d'un contenu", "1.0",
        "Un sujet est inscrit au calendrier éditorial", "Marketing & communication",
        "Contenu publié et suivi", "Aucune affirmation produit non confirmée",
        "Sujet sensible : validation de la direction",
        (Step("Rédiger le brouillon du contenu", "communication",
              proof="Brouillon rédigé"),
         Step("Relire et valider", "", (1,), "Contenu validé"),
         Step("Publier", "", (2,), "Lien de la publication"),
         Step("Relever les premiers résultats", "veille", (3,), "Chiffres notés"))),
    Procedure(
        "traitement-ticket", "Traitement d'un ticket de support", "1.0",
        "Un ticket est ouvert", "Support",
        "Ticket résolu ou escaladé", "Réponse appuyée sur un guide approuvé",
        "Guide absent : escalader à la direction du pôle",
        (Step("Chercher la réponse dans les guides", "support", proof="Guide cité ou absence"),
         Step("Préparer la réponse", "support", (1,), "Brouillon relu"),
         Step("Répondre au client et clore le ticket", "", (2,), "Ticket résolu"))),
    Procedure(
        "onboarding-partenaire", "Intégration d'un partenaire", "1.0",
        "Un partenaire est retenu", "IBIG PARTNERS",
        "Partenaire actif, informé des règles", "Règles de commission confirmées par la "
        "direction", "Partenaire sans pièces : suspendre l'activation",
        (Step("Vérifier les pièces et l'identité", "", proof="Pièces reçues"),
         Step("Préparer le message d'accueil et les règles", "communication", (1,),
              "Brouillon relu"),
         Step("Activer le partenaire", "", (2,), "Compte activé"))),
    Procedure(
        "rapport-quotidien", "Préparation du rapport quotidien", "1.0",
        "Chaque matin", "Direction générale",
        "Rapport lu par la direction", "Chiffres issus des journaux, jamais estimés",
        "Aucune activité : mentionner « rien à signaler »",
        (Step("Rassembler mails, validations, incidents et ventes", "chef",
              proof="Données du jour"),
         Step("Rédiger la synthèse", "chef", (1,), "Synthèse rédigée"),
         Step("Lire le rapport et noter les décisions", "", (2,), "Décisions notées"))),
)

BY_CODE = {p.code: p for p in PROCEDURES}


def get(code: str) -> Procedure | None:
    return BY_CODE.get(code)
