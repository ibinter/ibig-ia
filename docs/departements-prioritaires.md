# Départements prioritaires : un workflow par département (v2, phase 4)

Chaque département a une procédure versionnée (`src/ibig_agent/procedures.py`). Elle se lance depuis
la page **Procédures** : l'Autopilot crée la mission et ses tâches sans appel d'IA (aucun coût).
Aucune étape n'envoie, ne publie, n'active ni ne facture sans une étape humaine de validation.

| Département | Procédure (code) | Déclencheur |
|---|---|---|
| RH & recrutement | Traitement d'une candidature (`traitement-candidature`) | Une candidature arrive par mail |
| IBIG EDUFORM | Préparation d'une session (`preparation-session-formation`) | Une session est programmée |
| IBIG SOFT | Réponse à une demande de démonstration (`demande-demonstration-logiciel`) | Un prospect demande une démo |
| IBIG DIGITAL | Lancement d'un projet digital (`projet-digital`) | Un client valide une offre |
| Marketing & communication | Publication d'un contenu (`publication-contenu`) | Un sujet est au calendrier |
| Administration & finance | Relance d'une facture échue (`relance-facture-echue`) | Une échéance est dépassée |
| IBIG PARTNERS | Intégration d'un partenaire (`onboarding-partenaire`) | Un partenaire est retenu |

Test : `tests/test_autopilot.py::test_each_priority_department_has_a_runnable_workflow` lance les
sept procédures et vérifie responsable, résultat, qualité, exceptions, preuves et zéro appel d'IA.

Retour arrière : une procédure est une donnée du code ; revenir au commit précédent la retire. Les
missions déjà créées restent consultables.

À valider avec les pôles : le détail des étapes de « Lancement d'un projet digital » est une
proposition, à corriger selon la pratique réelle de DIGITAL.
