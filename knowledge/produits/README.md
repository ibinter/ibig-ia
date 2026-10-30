# Catalogue complet des produits IBIG (fichiers générés)

Ces fichiers ne se modifient **pas à la main** : ils sont régénérés par

```bash
ibig-agent importer-catalogue          # --essai pour compter sans rien écrire
```

à partir de l'API publique d'IBIG PARTNERS (`/api/catalogue/<marque>`), qui rassemble les
formations, solutions, services et produits de toutes les marques.

- L'agent les consulte **par recherche** (les entrées les plus proches de la question), jamais en
  bloc : il y en a plusieurs milliers.
- Le taux de commission des partenaires n'est jamais recopié (information interne).
- Le tarif « présentiel » et la grille de groupe d'EDUFORM, calculés par PARTNERS et différents de
  ceux du site de la marque, ne sont pas repris.
- Un produit présent plusieurs fois est fusionné ; si ses tarifs diffèrent, l'entrée porte la
  mention « tarif à confirmer avec l'équipe » : l'agent ne doit pas l'annoncer à un client sans
  vérification.
- Les classements par domaine d'EDUFORM sont automatiques (par mots-clés) : ils servent à ranger
  les fichiers, pas à faire foi.
- Un prix du catalogue ne valide un contenu que si le **nom du produit** y figure : un prix qui
  existe quelque part dans le catalogue n'autorise pas à l'appliquer à un autre produit.

Pour corriger une information, la corriger **à la source** (catalogue d'IBIG PARTNERS), puis
relancer l'import.
