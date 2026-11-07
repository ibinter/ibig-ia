"""Liste FERMÉE des actions de maintenance que l'agent peut demander au serveur.

Module volontairement sans aucune dépendance : l'exécuteur du serveur (deploy/console-actions.py) l'importe
pour savoir exactement quelle commande lancer. Rien d'autre ne peut être exécuté : une demande dont l'action
ou la cible n'est pas ici est refusée, même si la base de données était modifiée à la main.
"""

PM2 = {
    "ibig-digital": "IBIG DIGITAL (site)",
    "ibig-partners": "IBIG PARTNERS",
    "ibig-elearn": "IBIG E-LEARN",
    "ibig-api": "API d'IBIG IMMO TRUST",
    "lokativo": "LOKATIVO",
    "lokativo-demo": "LOKATIVO (démo)",
    "docpro": "DOCPRO",
    "agrifrik-erp": "AGRIFRIK (ERP)",
    "agrifrik-landing": "AGRIFRIK (vitrine)",
    "qrcode-express": "QR CODE EXPRESS",
}
DOCKER = {
    "santarex_backend": "SANTAREX (serveur)",
    "santarex_frontend": "SANTAREX (site)",
    "anouanze_api": "ANOUANZÊ (serveur)",
    "anouanze_web": "ANOUANZÊ (site)",
    "gestmoney_api": "GESTMONEY (serveur)",
    "gestmoney_web": "GESTMONEY (site)",
    "s-web-1": "SCOLABY",
}

# action -> {"label", "targets": {clé: {"label", "cmd": [commande exacte], "sensible": bool}}}
CATALOG = {
    "redemarrer": {
        "label": "Redémarrer un logiciel",
        "targets": {
            **{k: {"label": v, "cmd": ["pm2", "restart", k], "sensible": False} for k, v in PM2.items()},
            **{k: {"label": v, "cmd": ["docker", "restart", k], "sensible": False} for k, v in DOCKER.items()},
        },
    },
    "sauvegarder": {
        "label": "Lancer une sauvegarde maintenant",
        "targets": {
            "plateformes": {"label": "Bases de tous les logiciels",
                            "cmd": ["bash", "/opt/ibig-ia/deploy/sauvegarde-plateformes.sh"], "sensible": False},
        },
    },
    "serveur_web": {
        "label": "Recharger le serveur web",
        "targets": {
            "nginx": {"label": "nginx (vérifie la configuration avant)",
                      "cmd": ["bash", "-c", "nginx -t && nginx -s reload"], "sensible": True},
        },
    },
}


def command_for(action: str, target: str) -> list[str] | None:
    """Commande exacte pour (action, cible), ou None si elle n'est pas dans la liste fermée."""
    entry = CATALOG.get(action, {}).get("targets", {}).get(target)
    return list(entry["cmd"]) if entry else None
