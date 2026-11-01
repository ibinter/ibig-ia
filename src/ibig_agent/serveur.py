"""Analyse de l'état du serveur relevé par deploy/collecteur-ecosysteme.py (state/serveur.json).

Fonctions pures : elles transforment le relevé en problèmes avec cause, correction et risque. Le
centre de contrôle (ecosystem.py) les enregistre comme incidents. Aucun accès au serveur ici : l'agent
ne lit qu'un fichier déposé par le collecteur, qui tourne en lecture seule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

STALE_WARN_MIN = 10
STALE_CRIT_MIN = 60
DISK_WARN, DISK_CRIT = 85, 93
MEM_WARN_PCT, MEM_CRIT_PCT = 8, 4
LOAD_WARN, LOAD_CRIT = 1.5, 2.5            # charge sur 5 minutes par coeur
NGINX_SITE_WARN, NGINX_SITE_CRIT = 20, 100  # erreurs en 15 minutes pour un site
HTTP5XX_RATIO, HTTP5XX_MIN = 0.05, 20
SSH_FAILED_WARN = 5000
BACKUP_MAX_H = 36
RESTARTS_WARN = 10
IGNORED_UNITS = ("sys-kernel-debug.mount", "snap.cups", "cups", "wait-online")
CRITICAL_SERVICES = ("nginx", "docker", "cron", "mysql", "postgresql", "redis-server")


@dataclass
class Issue:
    key: str
    kind: str
    severity: str          # info | warning | critical
    title: str
    detail: str
    suggestion: str


def read(state_dir: Path) -> dict | None:
    try:
        return json.loads((Path(state_dir) / "serveur.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def age_minutes(d: dict, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(str(d.get("at", "")))
    except ValueError:
        return None
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    return (now - when).total_seconds() / 60


def _s(cause: str, fix: str, risk: str) -> str:
    return f"CAUSE PROBABLE : {cause}\nCORRECTION : {fix}\nRISQUE : {risk}"


def evaluate(d: dict | None, now: datetime) -> list[Issue]:
    if d is None:      # collecteur pas encore installé : le panneau l'indique, aucun incident
        return []
    out: list[Issue] = []

    def add(key, kind, sev, title, detail, sug):
        out.append(Issue(f"srv|{key}", kind, sev, title, detail, sug))

    age = age_minutes(d, now)
    if age is None or age > STALE_WARN_MIN:
        sev = "critical" if age is not None and age > STALE_CRIT_MIN else "warning"
        add("collecteur", "srv_collecteur", sev, "Serveur : le relevé n'est plus mis à jour",
            f"Dernier relevé il y a {age:.0f} minutes." if age is not None else "Date illisible.",
            _s("le collecteur est arrêté, bloqué, ou le serveur ne répond plus.",
               "vérifier `crontab -l`, le fichier /var/log/ibig-collecteur.log et l'état du serveur.",
               "moyen : les autres alertes du serveur sont aveugles tant que ce n'est pas réglé."))
    cores = max(int(d.get("cores", 1)), 1)
    for disk in d.get("disks", []):
        pct = disk.get("used_pct", 0)
        if pct >= DISK_WARN:
            sev = "critical" if pct >= DISK_CRIT else "warning"
            add(f"disque|{disk.get('mount')}", "srv_disque", sev,
                f"Serveur : disque {disk.get('mount')} plein à {pct:.0f} %",
                f"Il reste {disk.get('free_gb')} Go.",
                _s("journaux, sauvegardes ou images Docker qui s'accumulent.",
                   "vider les anciennes sauvegardes et images (`docker system df`), lire `du -sh /var/*`.",
                   "élevé : à 100 %, bases de données et sites s'arrêtent."))
    mem = d.get("mem", {})
    total = max(mem.get("total_mb", 1), 1)
    free_pct = 100 * mem.get("available_mb", total) / total
    if free_pct < MEM_WARN_PCT:
        sev = "critical" if free_pct < MEM_CRIT_PCT else "warning"
        add("memoire", "srv_memoire", sev, f"Serveur : mémoire presque épuisée ({free_pct:.0f} % libre)",
            f"{mem.get('available_mb')} Mo disponibles sur {total} Mo.",
            _s("une application consomme trop (fuite mémoire, trop de processus).",
               "identifier avec `docker stats` et `pm2 monit`, puis redémarrer l'application fautive.",
               "élevé : le système peut tuer des applications au hasard."))
    load5 = (d.get("load") or [0, 0, 0])[1] / cores
    if load5 > LOAD_WARN:
        sev = "critical" if load5 > LOAD_CRIT else "warning"
        add("charge", "srv_charge", sev, f"Serveur : charge élevée ({load5:.1f} par cœur sur 5 min)",
            f"Charge {d['load']} sur {cores} cœurs.",
            _s("un traitement lourd, une construction en cours, ou une application emballée.",
               "voir `top` ou `docker stats` pour trouver le processus gourmand.",
               "moyen : les sites ralentissent."))
    for c in d.get("containers", []):
        name, st, health = c.get("name", ""), c.get("state", ""), c.get("health", "")
        if "-run-" in name:
            continue
        if st != "running":
            add(f"conteneur|{name}", "srv_conteneur", "critical", f"Serveur : conteneur {name} arrêté ({st})",
                c.get("image", ""),
                _s("le conteneur s'est arrêté (erreur, manque de mémoire, redémarrage du serveur).",
                   f"lire `docker logs {name} --tail 100`, puis `docker start {name}` ou `docker compose up -d`.",
                   "élevé si c'est une base ou une API."))
        elif health == "unhealthy":
            add(f"conteneur|{name}", "srv_conteneur", "critical", f"Serveur : conteneur {name} en mauvaise santé",
                c.get("image", ""),
                _s("le contrôle de santé du conteneur échoue (application figée ou base injoignable).",
                   f"lire `docker logs {name} --tail 100` ; redémarrer avec `docker restart {name}`.",
                   "moyen à élevé."))
        elif c.get("restarts", 0) >= RESTARTS_WARN:
            add(f"conteneur|{name}", "srv_conteneur", "warning",
                f"Serveur : conteneur {name} redémarré {c['restarts']} fois", "",
                _s("l'application plante puis redémarre en boucle.",
                   f"lire `docker logs {name} --tail 200` pour trouver l'erreur à corriger.", "moyen."))
    for a in d.get("pm2", []):
        if a.get("status") != "online":
            add(f"pm2|{a['name']}", "srv_pm2", "critical", f"Serveur : application {a['name']} hors ligne ({a['status']})",
                "", _s("l'application Node s'est arrêtée ou a planté.",
                       f"`pm2 logs {a['name']} --lines 100` puis `pm2 restart {a['name']}`.", "élevé."))
        elif a.get("restarts", 0) >= 20:
            add(f"pm2|{a['name']}", "srv_pm2", "warning", f"Serveur : application {a['name']} redémarrée {a['restarts']} fois",
                "", _s("elle plante régulièrement.", f"`pm2 logs {a['name']} --err --lines 200`.", "moyen."))
    units = [u for u in d.get("failed_units", []) if not any(p in u for p in IGNORED_UNITS)]
    if units:
        add("unites", "srv_unites", "warning", f"Serveur : {len(units)} service(s) système en échec",
            ", ".join(units), _s("un service système n'a pas pu démarrer.",
                                  "`systemctl status <nom>` puis `journalctl -u <nom> -n 50`.", "variable."))
    ng = d.get("nginx", {})
    for site, n in sorted((ng.get("errors_15m_by_site") or {}).items()):
        if n >= NGINX_SITE_WARN:
            sev = "critical" if n >= NGINX_SITE_CRIT else "warning"
            add(f"nginx|{site}", "srv_nginx", sev, f"Serveur : {n} erreurs en 15 min sur {site}", "",
                _s("le site a un défaut qui revient (application arrêtée, délai dépassé, mauvaise configuration).",
                   f"lire `grep {site} /var/log/nginx/error.log | tail -20` et corriger la cause.", "moyen à élevé."))
    req, bad = ng.get("requests_15m", 0), ng.get("http5xx_15m", 0)
    if req >= HTTP5XX_MIN and bad / req > HTTP5XX_RATIO:
        add("http5xx", "srv_nginx", "warning", f"Serveur : {100 * bad / req:.0f} % de réponses en erreur ({bad}/{req})",
            "", _s("un ou plusieurs sites répondent en erreur 5xx.",
                   "voir la liste des sites en erreur dans les sondes et dans /var/log/nginx/error.log.", "moyen."))
    if d.get("security", {}).get("ssh_failed_24h", 0) >= SSH_FAILED_WARN:
        add("ssh", "srv_securite", "warning", f"Serveur : {d['security']['ssh_failed_24h']} tentatives SSH échouées en 24 h",
            "", _s("attaque automatique par force brute sur le port SSH.",
                   "passer à une clé SSH et désactiver la connexion par mot de passe (fail2ban bloque déjà).",
                   "moyen : le risque est un mot de passe deviné."))
    up = d.get("updates", {})
    if up.get("security", 0) > 0:
        add("maj_securite", "srv_maj", "warning", f"Serveur : {up['security']} mise(s) à jour de sécurité en attente",
            "", _s("des correctifs de sécurité du système ne sont pas installés.",
                   "`apt update && apt upgrade` en dehors des heures de pointe, puis redémarrer si demandé.",
                   "moyen : failles connues non corrigées."))
    elif up.get("pending", 0) >= 1:
        add("maj", "srv_maj", "info", f"Serveur : {up['pending']} mise(s) à jour en attente (non critiques)", "",
            _s("des mises à jour de confort ou de correction sont disponibles.",
               "les installer lors d'une fenêtre de maintenance.", "faible."))
    if d.get("reboot_required"):
        add("redemarrage", "srv_reboot", "warning", "Serveur : un redémarrage est nécessaire", "",
            _s("des mises à jour du système attendent un redémarrage pour être actives.",
               "planifier un redémarrage hors des heures de pointe (les conteneurs repartent seuls).",
               "faible : coupure de quelques minutes."))
    for b in d.get("backups", []):
        if b.get("age_h") is None:
            add(f"sauvegarde|{b['name']}", "srv_sauvegarde", "warning", f"Serveur : aucune sauvegarde trouvée pour {b['name']}",
                "", _s("le dossier est vide ou introuvable.", "vérifier le script et la tâche planifiée de cette sauvegarde.", "élevé."))
        elif b["age_h"] > BACKUP_MAX_H:
            add(f"sauvegarde|{b['name']}", "srv_sauvegarde", "critical",
                f"Serveur : sauvegarde {b['name']} trop ancienne ({b['age_h'] / 24:.1f} jours)", "",
                _s("la sauvegarde nocturne ne se fait plus (script cassé, dossier changé, plus d'espace).",
                   "lancer le script à la main, lire son journal, puis corriger la tâche planifiée.",
                   "élevé : en cas de panne, les données récentes seraient perdues."))
    for name, state in (d.get("services") or {}).items():
        if state != "active" and name in CRITICAL_SERVICES:
            add(f"service|{name}", "srv_service", "critical", f"Serveur : service {name} inactif ({state})", "",
                _s("le service système est arrêté.", f"`systemctl status {name}` puis `systemctl restart {name}`.", "élevé."))
    return out
