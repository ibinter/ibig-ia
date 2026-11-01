"""Catalogue de contrôles en LECTURE SEULE pour la base d'IBIG CORE (CRM, EDUFORM, Finance, RH, administration).

Chaque contrôle est un SELECT qui renvoie un nombre ; il devient un constat sur la page Anomalies quand il
dépasse son seuil. Les noms de tables et de colonnes viennent de la structure de la base du CORE (sauvegarde
du 13/05/2026) ; un contrôle dont une colonne a changé s'affiche « n'a pas pu s'exécuter » sans rien casser.
L'installation ajoute les contrôles absents (par nom) et ne touche pas aux autres.
"""

from __future__ import annotations

# (module, nom, requête, seuil, gravité)
CATALOG: list[tuple[str, str, str, float, str]] = [
    ("CRM", "Devis envoyés restés sans réponse depuis plus de 30 jours",
     "SELECT COUNT(*) FROM crm_devis WHERE statut = 'ENVOYE' AND date_devis < DATE_SUB(CURDATE(), INTERVAL 30 DAY)",
     0, "warning"),
    ("CRM", "Prospects actifs sans prochaine action ou en retard",
     ("SELECT COUNT(*) FROM crm_prospects WHERE actif = 1 AND statut IN ('CONTACTE','QUALIFIE','PROPOSITION','NEGOCIATION') "
      "AND (prochaine_action IS NULL OR prochaine_action < CURDATE())"), 20, "warning"),
    ("CRM", "Prospects actifs sans commercial responsable",
     "SELECT COUNT(*) FROM crm_prospects WHERE actif = 1 AND commercial_id IS NULL AND statut NOT IN ('GAGNE','PERDU')",
     0, "warning"),
    ("CRM", "Prospects en double (même adresse e-mail)",
     "SELECT COUNT(*) FROM (SELECT email FROM crm_prospects WHERE email <> '' GROUP BY email HAVING COUNT(*) > 1) d",
     0, "warning"),
    ("EDUFORM", "Tranches de paiement en retard",
     "SELECT COUNT(*) FROM edu_echeancier_tranches WHERE statut = 'EN_RETARD'", 0, "warning"),
    ("EDUFORM", "Inscriptions dont les montants ne concordent pas",
     ("SELECT COUNT(*) FROM edu_inscriptions WHERE statut = 'ACTIVE' "
      "AND (reste_a_payer < 0 OR montant_paye > montant_total OR montant_total - montant_paye <> reste_a_payer)"),
     0, "critical"),
    ("Finance", "Dettes fournisseurs en retard",
     "SELECT COUNT(*) FROM fin_dettes WHERE statut = 'EN_RETARD'", 0, "warning"),
    ("Finance", "Dettes dont le reste à payer est incohérent",
     "SELECT COUNT(*) FROM fin_dettes WHERE montant_total - montant_paye <> reste_a_payer", 0, "critical"),
    ("Finance", "Opérations restées en brouillon depuis plus de 30 jours",
     "SELECT COUNT(*) FROM fin_operations WHERE statut = 'DRAFT' AND created_at < DATE_SUB(NOW(), INTERVAL 30 DAY)",
     0, "warning"),
    ("RH", "Contrats arrivés à terme mais encore marqués en cours",
     "SELECT COUNT(*) FROM rh_contrats WHERE statut = 'EN_COURS' AND date_fin IS NOT NULL AND date_fin < CURDATE()",
     0, "warning"),
    ("RH", "Demandes de congé sans réponse depuis plus de 14 jours",
     "SELECT COUNT(*) FROM rh_conges WHERE statut = 'DEMANDE' AND requested_at < DATE_SUB(NOW(), INTERVAL 14 DAY)",
     0, "warning"),
    ("Administration", "Comptes administrateurs actifs non utilisés depuis 90 jours",
     ("SELECT COUNT(*) FROM admin_users WHERE is_active = 1 "
      "AND (last_login_at IS NULL OR last_login_at < DATE_SUB(NOW(), INTERVAL 90 DAY))"), 3, "warning"),
]


def install(monitor, connection: str, by: str) -> dict:
    """Ajoute à la connexion les contrôles du catalogue qui n'y sont pas encore (comparaison par nom)."""
    have = {c.label for c in monitor.checks() if c.connection == connection}
    added = skipped = 0
    for module, name, sql, threshold, severity in CATALOG:
        label = f"{module} : {name}"
        if label in have:
            skipped += 1
            continue
        monitor.add_check(connection, label, sql, threshold, severity, by)
        added += 1
    return {"added": added, "skipped": skipped}
