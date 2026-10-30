# Catalogue complet des produits IBIG (fichiers générés)

Les fichiers du catalogue ne sont **plus dans le dépôt** : ils sont générés depuis le **registre**
(page « Catalogue » du tableau de bord) dans `exports/catalogue/produits/` sur le serveur
(`knowledge/` est en lecture seule dans le conteneur). L'agent lit les deux dossiers. Ils ne se
modifient pas à la main.

```bash
ibig-agent importer-catalogue          # --essai pour compter sans rien écrire
```

relit l'API publique d'IBIG PARTNERS (`/api/catalogue/<marque>`) et les pages du site EDUFORM,
met le registre à jour (versions, historique des tarifs) puis régénère les fichiers. Il tourne
aussi chaque dimanche à 3 h 30, et les fichiers sont régénérés depuis le registre à chaque
démarrage de l'agent.

- L'agent les consulte **par recherche** (les entrées les plus proches de la question), jamais en
  bloc : il y en a plusieurs milliers.
- Le taux de commission des partenaires n'est jamais recopié (information interne).
- Le tarif « présentiel » et la grille de groupe d'EDUFORM, calculés par PARTNERS et différents de
  ceux du site de la marque, ne sont pas repris.
- Un produit présent plusieurs fois est fusionné ; si ses tarifs diffèrent, l'offre est « à
  confirmer » : l'agent ne l'annonce pas sans vérification. La Direction fixe le tarif ou le statut
  dans la page « Catalogue » ; une décision humaine n'est jamais défaite par un import.
- Une offre suspendue, indisponible ou archivée n'est jamais proposée à l'agent.
- Un prix du catalogue ne valide un contenu que si le **nom du produit** y figure.

Pour corriger une information à la source, la corriger dans le catalogue d'IBIG PARTNERS, puis
relancer l'import.
