"""Centre de contrôle de l'écosystème IBIG : inventaire, sondes, incidents et corrections suggérées.

Ce que fait ce module :
  - il tient l'inventaire des sites, logiciels et API (config/ecosysteme.yaml + ajouts depuis la page) ;
  - toutes les 5 minutes, il teste chaque actif comme un visiteur (réponse, vitesse, certificat, mot
    attendu) et conserve l'historique 90 jours : disponibilité sur 24 h, courbes de vitesse ;
  - une fois par jour, il contrôle l'hygiène (redirection HTTPS, HSTS, robots.txt, sitemap) ;
  - il ouvre un incident seulement après deux échecs de suite (pas de fausse alerte sur un raté
    isolé), le referme tout seul au retour à la normale, et alerte la direction pour un incident
    critique d'un actif de criticité haute ;
  - chaque incident porte la cause probable, la correction suggérée et le risque : l'agent PROPOSE,
    il n'applique jamais une correction lui-même.
Il ne modifie rien chez les actifs : il ne fait que les lire comme le ferait un visiteur.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from . import serveur
from .anomalies import cert_days
from .config import Settings
from .configstore import service_value, set_service_value
from .db import EcoAsset, EcoCheck, EcoIncident, utcnow
from .governance import as_utc

FAIL_BEFORE_INCIDENT = 2          # échecs de suite avant d'ouvrir un incident de disponibilité
SLOW_RUNS = 3                     # mesures lentes de suite avant d'ouvrir un incident de vitesse
SLOW_MS = {"haute": 3000, "moyenne": 4000, "basse": 6000}
CERT_WARN_DAYS = 14
CERT_CRITICAL_DAYS = 3
RETENTION_DAYS = 90
HEARTBEAT_URL = "eco_heartbeat_url"
HEARTBEAT_STATE = "eco_heartbeat_state"
PROBE_THREADS = 6
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
SEVERITY_LABELS = {"info": "Amélioration", "warning": "À corriger", "critical": "Urgent"}
KIND_LABELS = {"dispo": "Disponibilité", "vitesse": "Vitesse", "cert": "Certificat",
               "contenu": "Contenu", "http_redirect": "Sécurité", "hsts": "Sécurité",
               "robots": "Référencement", "sitemap": "Référencement",
               "srv_collecteur": "Serveur", "srv_disque": "Serveur", "srv_memoire": "Serveur",
               "srv_charge": "Serveur", "srv_conteneur": "Serveur", "srv_pm2": "Serveur",
               "srv_unites": "Serveur", "srv_nginx": "Serveur", "srv_securite": "Serveur",
               "srv_maj": "Serveur", "srv_reboot": "Serveur", "srv_sauvegarde": "Sauvegarde",
               "srv_service": "Serveur", "srv_offsite": "Sauvegarde", "anom_donnees": "Base de données", "anom_bug": "Bug",
               "anom_contacts": "Contenu du site", "anom_liens": "Liens", "anom_sauvegarde": "Sauvegarde"}
KINDS = ("site", "logiciel", "api", "autre")
HOSTINGS = ("vps", "ftp", "externe")
CRITICALITIES = ("haute", "moyenne", "basse")

HTACCESS_HTTPS = ("RewriteEngine On\nRewriteCond %{HTTPS} off\n"
                  "RewriteRule ^(.*)$ https://%{HTTP_HOST}/$1 [L,R=301]")
HTACCESS_HSTS = 'Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"'


class EcosystemError(ValueError):
    pass


@dataclass
class ProbeResult:
    ok: bool
    status: int = 0
    ms: int = 0
    cert_days: int | None = None
    detail: str = ""
    missing_text: bool = False


def parse_codes(spec: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{3})\s*(?:-\s*(\d{3}))?\s*", spec or "")
    if not m:
        return 200, 399
    low = int(m.group(1))
    return low, int(m.group(2) or low)


def suggestion(kind: str, asset: EcoAsset, detail: str = "", status: int = 0,
               cert: int | None = None) -> str:
    """Cause probable, correction suggérée et risque, selon la nature de l'incident."""
    host = urlparse(asset.url).netloc
    if kind == "dispo":
        if status in (502, 503, 504) and asset.hosting == "vps":
            return (f"CAUSE PROBABLE : l'application de {asset.name} ne répond plus derrière le serveur web "
                    "(conteneur ou processus arrêté, ou port changé).\n"
                    "CORRECTION : sur le serveur, vérifier `docker ps` et `pm2 list`, lire les derniers "
                    "journaux, puis relancer l'application (`docker compose up -d` ou `pm2 restart <nom>`).\n"
                    "RISQUE : faible, coupure de quelques secondes au redémarrage.")
        if status == 0:
            return (f"CAUSE PROBABLE : {host} est injoignable (serveur arrêté, DNS ou pare-feu, ou délai dépassé).\n"
                    "CORRECTION : vérifier que le serveur ou l'hébergement est en ligne, que le nom de domaine "
                    "pointe toujours vers la bonne adresse et que le certificat n'a pas expiré.\n"
                    "RISQUE : élevé si le site est public, les visiteurs ne peuvent pas y accéder.")
        if status == 404:
            return ("CAUSE PROBABLE : la page testée n'existe plus (déploiement incomplet ou fichier supprimé).\n"
                    "CORRECTION : vérifier le dernier déploiement ; pour une API, définir une adresse de santé "
                    "dans l'inventaire.\nRISQUE : moyen.")
        if status == 403:
            return ("CAUSE PROBABLE : accès refusé (droits des fichiers ou règle de pare-feu).\n"
                    "CORRECTION : vérifier les droits des fichiers du site et les règles d'accès (.htaccess).\n"
                    "RISQUE : élevé, le site est inaccessible aux visiteurs.")
        if 500 <= status < 600 and asset.hosting == "ftp":
            return ("CAUSE PROBABLE : erreur PHP sur l'hébergement mutualisé (fichier modifié, version de PHP, "
                    "base de données).\nCORRECTION : lire le fichier `error_log` du dossier du site, "
                    "annuler la dernière modification ou restaurer la sauvegarde.\nRISQUE : élevé.")
        return (f"CAUSE PROBABLE : réponse anormale ({status or 'aucune'}) : {detail or 'non précisée'}.\n"
                "CORRECTION : ouvrir le site, lire les journaux d'erreurs et comparer avec la dernière version "
                "en bon état.\nRISQUE : moyen à élevé.")
    if kind == "contenu":
        return ("CAUSE PROBABLE : la page répond, mais le texte attendu a disparu (page vide, remplacée, en "
                "maintenance ou piratée).\nCORRECTION : ouvrir la page et vérifier immédiatement son contenu ; "
                "en cas de doute, comparer avec la sauvegarde.\nRISQUE : élevé (image et sécurité).")
    if kind == "vitesse":
        return (f"CAUSE PROBABLE : {asset.name} répond lentement (charge du serveur, base de données, images ou "
                "scripts lourds, hébergement saturé).\nCORRECTION : vérifier la charge, activer le cache, "
                "compresser les images, examiner les requêtes lentes.\nRISQUE : faible à moyen "
                "(abandon des visiteurs, mauvais référencement).")
    if kind == "cert":
        where = ("`certbot renew` sur le serveur" if asset.hosting == "vps"
                 else "le renouvellement du certificat (AutoSSL) chez l'hébergeur")
        return (f"CAUSE PROBABLE : le certificat HTTPS de {host} expire dans {cert} jour(s).\n"
                f"CORRECTION : lancer {where} et vérifier que le renouvellement automatique est actif.\n"
                "RISQUE : élevé à l'expiration, les navigateurs affichent un avertissement de sécurité.")
    if kind == "http_redirect":
        return (f"CAUSE PROBABLE : {host} reste accessible en HTTP non chiffré.\n"
                f"CORRECTION : ajouter dans le fichier `.htaccess` du site :\n{HTACCESS_HTTPS}\n"
                "RISQUE : très faible, à tester d'abord sur une copie.")
    if kind == "hsts":
        return ("CAUSE PROBABLE : l'en-tête de sécurité HSTS est absent.\n"
                f"CORRECTION : ajouter `{HTACCESS_HSTS}` (Apache) ou l'équivalent dans nginx.\n"
                "RISQUE : faible, à activer seulement quand tout le site fonctionne en HTTPS.")
    if kind == "robots":
        return ("CAUSE PROBABLE : aucun fichier `robots.txt`.\nCORRECTION : en ajouter un à la racine du site "
                "(même vide) pour guider les moteurs de recherche.\nRISQUE : nul.")
    if kind == "sitemap":
        return ("CAUSE PROBABLE : aucun `sitemap.xml`.\nCORRECTION : générer un plan du site et le déclarer "
                "dans `robots.txt` pour améliorer le référencement.\nRISQUE : nul.")
    return "Aucune suggestion automatique : demander un diagnostic à l'équipe technique."


class EcosystemService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings,
                 alert: Callable[[str, str, str], None] | None = None,
                 client: httpx.Client | None = None,
                 cert_check: Callable[[str], int | None] = cert_days,
                 findings: Callable[[], list] | None = None, llm=None) -> None:
        self._sessions = sessions
        self.settings = settings
        self.alert = alert
        self._client = client
        self.cert_check = cert_check
        self.findings = findings
        self.llm = llm

    # ------------------------------------------------------------------ inventaire
    def sync_config(self, path: Path | None = None) -> dict:
        """Importe config/ecosysteme.yaml : crée les actifs absents, met à jour les autres."""
        path = path or Path(self.settings.config_dir) / "ecosysteme.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except OSError:
            return {"created": 0, "updated": 0, "missing_file": True}
        created = updated = 0
        with self._sessions() as s:
            for it in data.get("actifs", []):
                url = str(it.get("url", "")).strip().rstrip("/")
                if not url.startswith(("http://", "https://")):
                    continue
                fields = {
                    "name": str(it.get("nom") or urlparse(url).netloc)[:200],
                    "kind": it.get("genre", "site") if it.get("genre") in KINDS else "site",
                    "hosting": it.get("hebergement", "vps") if it.get("hebergement") in HOSTINGS else "vps",
                    "pole": str(it.get("pole", ""))[:40],
                    "criticality": (it.get("criticite", "moyenne")
                                    if it.get("criticite") in CRITICALITIES else "moyenne"),
                    "health_path": str(it.get("chemin", "/"))[:200] or "/",
                    "ok_codes": str(it.get("codes_ok", "200-399"))[:20],
                    "expect_text": str(it.get("attendu", ""))[:300],
                    "notes": str(it.get("notes", "")),
                }
                row = s.scalar(select(EcoAsset).where(EcoAsset.url == url))
                if row is None:
                    s.add(EcoAsset(url=url, active=bool(it.get("actif", True)), source="config", **fields))
                    created += 1
                else:
                    for k, v in fields.items():
                        setattr(row, k, v)
                    updated += 1
            s.commit()
        return {"created": created, "updated": updated, "missing_file": False}

    def assets(self, active_only: bool = False) -> list[EcoAsset]:
        with self._sessions() as s:
            q = select(EcoAsset).order_by(EcoAsset.pole, EcoAsset.name)
            if active_only:
                q = q.where(EcoAsset.active.is_(True))
            return list(s.scalars(q).all())

    def add_asset(self, name: str, url: str, kind: str = "site", hosting: str = "vps", pole: str = "",
                  criticality: str = "moyenne", health_path: str = "/", expect_text: str = "") -> int:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")) or " " in url:
            raise EcosystemError("Adresse invalide : elle doit commencer par https://")
        if kind not in KINDS or hosting not in HOSTINGS or criticality not in CRITICALITIES:
            raise EcosystemError("Genre, hébergement ou criticité inconnu")
        name = " ".join(name.split())[:200] or urlparse(url).netloc
        with self._sessions() as s:
            if s.scalar(select(EcoAsset).where(EcoAsset.url == url)) is not None:
                raise EcosystemError("Cette adresse est déjà dans l'inventaire")
            row = EcoAsset(name=name, url=url, kind=kind, hosting=hosting, pole=pole[:40],
                           criticality=criticality, health_path=health_path or "/",
                           expect_text=expect_text[:300], source="manuel")
            s.add(row)
            s.commit()
            return row.id

    def import_text(self, text: str) -> dict:
        """Une ligne par actif : « nom | adresse | hébergement | pôle | criticité » (les 3 derniers
        champs sont facultatifs). Les lignes invalides sont comptées, pas fatales."""
        added = rejected = 0
        for line in (ln.strip() for ln in text.splitlines()):
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")] + ["", "", "", ""]
            if parts[0].startswith("http"):                      # simple liste d'adresses
                parts = [urlparse(parts[0]).netloc, *parts]
            name, url, hosting, pole, crit = parts[:5]
            try:
                self.add_asset(name, url, hosting=hosting or "vps", pole=pole,
                               criticality=crit or "moyenne")
                added += 1
            except EcosystemError:
                rejected += 1
        return {"added": added, "rejected": rejected}

    def set_active(self, asset_id: int, active: bool) -> None:
        with self._sessions() as s:
            row = s.get(EcoAsset, asset_id)
            if row is None:
                raise EcosystemError("Actif introuvable")
            row.active = active
            s.commit()

    # ------------------------------------------------------------------ sondes
    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(
            timeout=12, follow_redirects=True,
            headers={"User-Agent": "IBIG-Controle/1.0 (lecture seule)"})

    def probe(self, a: EcoAsset, client: httpx.Client) -> ProbeResult:
        url = a.url + (a.health_path if a.health_path.startswith("/") else "/" + a.health_path)
        low, high = parse_codes(a.ok_codes)
        started = time.monotonic()
        try:
            resp = client.get(url)
        except httpx.HTTPError as exc:
            return ProbeResult(False, 0, int((time.monotonic() - started) * 1000),
                               detail=f"injoignable ({exc.__class__.__name__})")
        ms = int((time.monotonic() - started) * 1000)
        cert = self.cert_check(urlparse(a.url).netloc) if a.url.startswith("https://") else None
        ok = low <= resp.status_code <= high
        missing = False
        detail = f"HTTP {resp.status_code}"
        if ok and a.expect_text and a.expect_text.lower() not in resp.text.lower():
            ok, missing = False, True
            detail = f"HTTP {resp.status_code} mais le texte « {a.expect_text} » est absent"
        return ProbeResult(ok, resp.status_code, ms, cert, detail, missing)

    def run_probes(self, now: datetime | None = None) -> dict:
        """Teste tous les actifs suivis, enregistre l'historique et met les incidents à jour."""
        now = now or utcnow()
        if not self.assets():          # premier lancement : charger l'inventaire du fichier
            self.sync_config()
        assets = self.assets(active_only=True)
        client = self._http()
        try:
            with ThreadPoolExecutor(PROBE_THREADS) as pool:
                results = list(pool.map(lambda a: (a, self.probe(a, client)), assets))
        finally:
            if self._client is None:
                client.close()
        opened: list[EcoIncident] = []
        with self._sessions() as s:
            for a, r in results:
                s.add(EcoCheck(asset_id=a.id, at=now, ok=r.ok, status=r.status, ms=r.ms,
                               cert_days=r.cert_days, detail=r.detail[:300]))
            s.flush()
            for a, r in results:
                opened += self._evaluate(s, a, r, now)
            s.commit()
            opened = [i for i in opened if i.id is not None]
        server_opened = self.run_server(now) + self.run_findings(now)
        self._notify(opened + server_opened)
        self.heartbeat(now)
        return {"checked": len(assets), "ok": sum(r.ok for _, r in results),
                "failing": sum(not r.ok for _, r in results),
                "new_incidents": len(opened) + len(server_opened)}

    def run_server(self, now: datetime | None = None) -> list[EcoIncident]:
        """Analyse le relevé du serveur (state/serveur.json) : ouvre, met à jour ou ferme les incidents `srv|…`."""
        now = now or utcnow()
        issues = serveur.evaluate(serveur.read(self.settings.state_dir), now)
        current = {i.key for i in issues}
        opened: list[EcoIncident] = []
        with self._sessions() as s:
            for i in issues:
                row = self._upsert_raw(s, None, i.key, i.kind, i.severity, i.title, i.detail, i.suggestion, now)
                if row is not None and row.id is not None:
                    opened.append(row)
            for row in s.scalars(select(EcoIncident).where(EcoIncident.key.like("srv|%"),
                                                           EcoIncident.status != "resolved")).all():
                if row.key not in current:
                    row.status = "resolved"
            s.commit()
        return opened

    def run_findings(self, now: datetime | None = None) -> list[EcoIncident]:
        """Reprend dans le centre de contrôle les constats ouverts des bases de données, des bugs et des
        sites (module anomalies), pour que tout soit lu au même endroit."""
        if self.findings is None:
            return []
        now = now or utcnow()
        wanted = {f"anom|{f.key}": f for f in self.findings() if f.status == "open"}
        opened: list[EcoIncident] = []
        with self._sessions() as s:
            for key, f in wanted.items():
                sev = f.severity if f.severity in SEVERITY_RANK else "warning"
                text = (f"CAUSE PROBABLE : {f.detail or f.title}\nCORRECTION : ouvrir la page Anomalies pour "
                        f"le détail et la procédure.\nRISQUE : selon le constat ({SEVERITY_LABELS[sev].lower()}).")
                row = self._upsert_raw(s, None, key, f"anom_{f.check}", sev, f.title, f.detail, text, now)
                if row is not None and row.id is not None:
                    opened.append(row)
            for row in s.scalars(select(EcoIncident).where(EcoIncident.key.like("anom|%"),
                                                           EcoIncident.status != "resolved")).all():
                if row.key not in wanted:
                    row.status = "resolved"
            s.commit()
        return opened

    # ------------------------------------------------------------------ diagnostic par l'IA (à la demande)
    def diagnose(self, incident_id: int, by: str) -> str:
        """Demande à l'IA une analyse plus précise d'un incident. À la demande seulement (coût maîtrisé).
        L'IA ne reçoit que le titre, le détail, les dernières mesures et les chiffres du serveur."""
        if self.llm is None:
            raise EcosystemError("L'IA n'est pas configurée")
        with self._sessions() as s:
            inc = s.get(EcoIncident, incident_id)
            if inc is None:
                raise EcosystemError("Incident introuvable")
            history = []
            if inc.asset_id:
                a = s.get(EcoAsset, inc.asset_id)
                history = [f"{c.at:%H:%M} {'ok' if c.ok else 'ÉCHEC'} {c.status} {c.ms} ms {c.detail}"
                           for c in self._last(s, a, 8)] if a else []
            facts = f"Incident : {inc.title}\nDétail : {inc.detail}\nVu {inc.count} fois depuis {inc.first_seen:%d/%m %H:%M}"
        srv = self.server_state()
        if srv:
            facts += (f"\nServeur : charge {srv['load']} sur {srv['cores']} cœurs, mémoire libre "
                      f"{srv['mem_free_pct']} %, disque {srv['disk_pct']} %, conteneurs {srv['containers'][0]}/"
                      f"{srv['containers'][1]}, erreurs web 15 min {srv['errors_15m']}, services en échec "
                      f"{', '.join(srv['failed_units']) or 'aucun'}")
        if history:
            facts += "\nDernières mesures :\n" + "\n".join(history)
        try:
            text = self.llm.write(
                "ecosysteme.diagnostic",
                "Tu es l'ingénieur d'exploitation d'IBIG SARL (Abidjan). À partir des seuls faits fournis, "
                "donne en français simple : 1) la cause la plus probable et pourquoi, 2) les 2 à 4 commandes ou "
                "gestes de vérification, dans l'ordre, 3) la correction, 4) comment confirmer que c'est réglé. "
                "N'invente aucun fait ; si les données ne suffisent pas, dis quoi relever. Moins de 180 mots.",
                facts, 700).strip()
        except Exception as exc:
            raise EcosystemError(f"Analyse impossible pour le moment ({exc.__class__.__name__})") from exc
        set_service_value(self._sessions, self.settings, f"eco_diag_{incident_id}", text[:3000], by)
        return text

    def fix_plan(self, incident_id: int) -> tuple[str, str, list[tuple[str, str]]]:
        """Plan de correction d'un incident (titre, objectif, étapes) à confier à l'Autopilot, qui le
        suit avec preuves. Rien n'est exécuté sur les systèmes : ce sont des tâches à valider."""
        with self._sessions() as s:
            inc = s.get(EcoIncident, incident_id)
        if inc is None:
            raise EcosystemError("Incident introuvable")
        lines = {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in inc.suggestion.splitlines())}
        ai = service_value(self._sessions, self.settings, f"eco_diag_{incident_id}")
        steps = [
            ("Confirmer le constat et la cause", lines.get("CAUSE PROBABLE", "") + (f"\n\nAnalyse IA :\n{ai}" if ai else "")),
            ("Appliquer la correction", lines.get("CORRECTION", "Voir l'incident.") + "\nRisque : " + lines.get("RISQUE", "")),
            ("Vérifier le retour à la normale", "L'incident disparaît du centre de contrôle et la mesure suivante est verte."),
        ]
        return f"Corriger : {inc.title}"[:200], f"Rétablir un fonctionnement normal : {inc.title}", steps

    def link_mission(self, incident_id: int, mission_id: int, by: str) -> None:
        set_service_value(self._sessions, self.settings, f"eco_mission_{incident_id}", str(mission_id), by)

    def missions_for(self, ids: list[int]) -> dict[int, int]:
        out = {}
        for i in ids:
            v = service_value(self._sessions, self.settings, f"eco_mission_{i}")
            if v.isdigit():
                out[i] = int(v)
        return out

    def diagnoses(self, ids: list[int]) -> dict[int, str]:
        return {i: v for i in ids if (v := service_value(self._sessions, self.settings, f"eco_diag_{i}"))}

    # ------------------------------------------------------------------ sonde externe (battement)
    def set_heartbeat_url(self, url: str, by: str) -> None:
        """Adresse de surveillance externe (healthchecks.io, Better Stack…) : l'agent la contacte à chaque
        mesure ; si le serveur tombe, c'est ce service extérieur qui prévient, faute de battement."""
        url = url.strip()
        if url and not url.startswith("https://"):
            raise EcosystemError("Adresse invalide : elle doit commencer par https://")
        set_service_value(self._sessions, self.settings, HEARTBEAT_URL, url, by, secret=True)
        set_service_value(self._sessions, self.settings, HEARTBEAT_STATE, "", by)

    def heartbeat(self, now: datetime | None = None) -> bool | None:
        """Envoie le battement ; None si aucune adresse n'est réglée."""
        url = service_value(self._sessions, self.settings, HEARTBEAT_URL)
        if not url:
            return None
        client = self._http()
        try:
            ok = client.get(url).status_code < 400
            detail = "" if ok else "refusé par le service"
        except httpx.HTTPError as exc:
            ok, detail = False, f"injoignable ({exc.__class__.__name__})"
        finally:
            if self._client is None:
                client.close()
        state = {"at": (now or utcnow()).isoformat(), "ok": ok, "detail": detail}
        set_service_value(self._sessions, self.settings, HEARTBEAT_STATE, json.dumps(state), "agent")
        return ok

    def heartbeat_status(self) -> dict:
        url = service_value(self._sessions, self.settings, HEARTBEAT_URL)
        try:
            state = json.loads(service_value(self._sessions, self.settings, HEARTBEAT_STATE) or "{}")
        except ValueError:
            state = {}
        return {"configured": bool(url), "host": urlparse(url).netloc if url else "", **state}

    def server_state(self, now: datetime | None = None) -> dict | None:
        """Chiffres du relevé pour le panneau « Serveur » (None si aucun relevé)."""
        now = now or utcnow()
        d = serveur.read(self.settings.state_dir)
        if d is None:
            return None
        mem = d.get("mem", {})
        age = serveur.age_minutes(d, now)
        return {
            "age_min": None if age is None else round(age), "host": d.get("hostname", ""),
            "uptime_days": round(d.get("uptime_s", 0) / 86400, 1), "load": d.get("load", []),
            "cores": d.get("cores", 1),
            "mem_free_pct": round(100 * mem.get("available_mb", 0) / max(mem.get("total_mb", 1), 1)),
            "disk_pct": max((x.get("used_pct", 0) for x in d.get("disks", [])), default=0),
            "containers": [len([c for c in d.get("containers", []) if c.get("state") == "running"]),
                           len(d.get("containers", []))],
            "pm2": [len([a for a in d.get("pm2", []) if a.get("status") == "online"]), len(d.get("pm2", []))],
            "backups": d.get("backups", []), "failed_units": [u for u in d.get("failed_units", [])
                             if not any(p in u for p in serveur.IGNORED_UNITS)],
            "updates": d.get("updates", {}), "errors_15m": d.get("nginx", {}).get("errors_15m_total", 0),
        }

    # ------------------------------------------------------------------ incidents
    def _upsert(self, s: Session, a: EcoAsset, kind: str, severity: str, title: str, detail: str,
                text: str, now: datetime) -> EcoIncident | None:
        """Ouvre ou met à jour un incident ; renvoie l'incident s'il est nouveau, rouvert ou aggravé."""
        return self._upsert_raw(s, a.id, f"{a.id}|{kind}", kind, severity, title, detail, text, now)

    def _upsert_raw(self, s: Session, asset_id: int | None, key: str, kind: str, severity: str,
                    title: str, detail: str, text: str, now: datetime) -> EcoIncident | None:
        row = s.scalar(select(EcoIncident).where(EcoIncident.key == key))
        if row is None:
            row = EcoIncident(asset_id=asset_id, key=key, kind=kind, severity=severity, title=title[:300],
                              detail=detail, suggestion=text, first_seen=now, last_seen=now)
            s.add(row)
            s.flush()
            return row
        worse = SEVERITY_RANK[severity] > SEVERITY_RANK.get(row.severity, 0)
        reopened = row.status == "resolved"
        row.title, row.detail, row.suggestion = title[:300], detail, text
        row.severity, row.last_seen = severity, now
        if reopened:
            row.status, row.count, row.first_seen, row.ack_by = "open", 1, now, ""
        else:
            row.count += 1
        return row if (reopened or worse) else None

    def _resolve(self, s: Session, a: EcoAsset, kind: str) -> None:
        row = s.scalar(select(EcoIncident).where(EcoIncident.key == f"{a.id}|{kind}"))
        if row is not None and row.status != "resolved":
            row.status = "resolved"

    def _last(self, s: Session, a: EcoAsset, n: int) -> list[EcoCheck]:
        return list(s.scalars(select(EcoCheck).where(EcoCheck.asset_id == a.id)
                              .order_by(EcoCheck.at.desc(), EcoCheck.id.desc()).limit(n)).all())

    def _evaluate(self, s: Session, a: EcoAsset, r: ProbeResult, now: datetime) -> list[EcoIncident]:
        opened: list[EcoIncident] = []
        crit = a.criticality
        urgent = "critical" if crit == "haute" else "warning"

        def add(inc: EcoIncident | None) -> None:
            if inc is not None:
                opened.append(inc)

        # Disponibilité et contenu : deux échecs de suite avant d'ouvrir (pas de fausse alerte)
        recent = self._last(s, a, FAIL_BEFORE_INCIDENT)
        failing = len(recent) >= FAIL_BEFORE_INCIDENT and all(not c.ok for c in recent)
        if r.ok:
            self._resolve(s, a, "dispo")
            self._resolve(s, a, "contenu")
        elif failing and r.missing_text:
            add(self._upsert(s, a, "contenu", urgent, f"{a.name} : le contenu attendu a disparu",
                             r.detail, suggestion("contenu", a), now))
        elif failing:
            label = f"répond en erreur {r.status}" if r.status else "est injoignable"
            add(self._upsert(s, a, "dispo", urgent, f"{a.name} {label}", r.detail,
                             suggestion("dispo", a, r.detail, r.status), now))
        # Vitesse : trois mesures lentes de suite
        if r.ok:
            slow = [c for c in self._last(s, a, SLOW_RUNS) if c.ok and c.ms > SLOW_MS[crit]]
            if len(slow) >= SLOW_RUNS:
                add(self._upsert(s, a, "vitesse", "warning", f"{a.name} est lent ({r.ms / 1000:.1f} s)",
                                 f"Seuil : {SLOW_MS[crit] / 1000:g} s", suggestion("vitesse", a), now))
            elif r.ms <= SLOW_MS[crit]:
                self._resolve(s, a, "vitesse")
        # Certificat
        if r.cert_days is not None and r.cert_days < CERT_WARN_DAYS:
            sev = "critical" if r.cert_days < CERT_CRITICAL_DAYS and crit != "basse" else "warning"
            add(self._upsert(s, a, "cert", sev,
                             f"{a.name} : certificat expire dans {max(r.cert_days, 0)} jour(s)",
                             "", suggestion("cert", a, cert=r.cert_days), now))
        elif r.cert_days is not None:
            self._resolve(s, a, "cert")
        return opened

    def _notify(self, opened: list[EcoIncident]) -> None:
        urgent = [i for i in opened if i.severity == "critical"]
        if not urgent or self.alert is None:
            return
        body = "\n\n".join(f"- {i.title}\n  {i.suggestion.splitlines()[0] if i.suggestion else ''}"
                           for i in urgent[:8])
        self.alert("ecosysteme", f"[IBIG] {len(urgent)} incident(s) urgent(s) sur l'écosystème",
                   body + "\n\nDétail et corrections suggérées : page Écosystème du tableau de bord.")

    def incidents(self, include_resolved: bool = False) -> list[EcoIncident]:
        with self._sessions() as s:
            q = select(EcoIncident).order_by(EcoIncident.last_seen.desc())
            if not include_resolved:
                q = q.where(EcoIncident.status != "resolved")
            rows = list(s.scalars(q).all())
        rows.sort(key=lambda i: (-SEVERITY_RANK.get(i.severity, 0), i.status == "ack"))
        return rows

    def set_incident_status(self, incident_id: int, status: str, by: str) -> None:
        if status not in ("open", "ack", "resolved"):
            raise EcosystemError("Statut inconnu")
        with self._sessions() as s:
            row = s.get(EcoIncident, incident_id)
            if row is None:
                raise EcosystemError("Incident introuvable")
            row.status, row.ack_by = status, by
            s.commit()

    # ------------------------------------------------------------------ hygiène (quotidienne)
    def run_hygiene(self, now: datetime | None = None) -> dict:
        """Contrôle de bonnes pratiques : HTTPS forcé, HSTS, robots.txt, sitemap.xml."""
        now = now or utcnow()
        client = self._http()
        opened = 0
        sev = {"http_redirect": "warning", "hsts": "info", "robots": "info", "sitemap": "info"}
        try:
            targets = [a for a in self.assets(active_only=True)
                       if a.url.startswith("https://") and a.kind != "api" and a.criticality != "basse"]
            with self._sessions() as s:
                for a in targets:
                    host = urlparse(a.url).netloc
                    titles = {"http_redirect": f"{a.name} : pas de redirection HTTP vers HTTPS",
                              "hsts": f"{a.name} : en-tête HSTS absent",
                              "robots": f"{a.name} : pas de robots.txt",
                              "sitemap": f"{a.name} : pas de sitemap.xml"}
                    for kind, good in self._hygiene_facts(client, a, host).items():
                        if good is None:
                            continue
                        if good:
                            self._resolve(s, a, kind)
                        elif self._upsert(s, a, kind, sev[kind], titles[kind], "", suggestion(kind, a), now):
                            opened += 1
                s.commit()
        finally:
            if self._client is None:
                client.close()
        return {"opened": opened}

    @staticmethod
    def _hygiene_facts(client: httpx.Client, a: EcoAsset, host: str) -> dict[str, bool | None]:
        facts: dict[str, bool | None] = {"http_redirect": None, "hsts": None, "robots": None, "sitemap": None}
        try:
            home = client.get(a.url + "/")
            facts["hsts"] = "strict-transport-security" in home.headers
            plain = client.get("http://" + host + "/", follow_redirects=False)
            facts["http_redirect"] = plain.status_code in (301, 302, 307, 308)
            rb = client.get(a.url + "/robots.txt")
            facts["robots"] = rb.status_code == 200 and "html" not in rb.headers.get("content-type", "")
            sm = client.get(a.url + "/sitemap.xml")
            facts["sitemap"] = sm.status_code == 200 and "xml" in sm.headers.get("content-type", "")
        except httpx.HTTPError:
            pass
        return facts

    # ------------------------------------------------------------------ vue d'ensemble
    def summary(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        since = now - timedelta(hours=24)
        out_assets: list[dict] = []
        with self._sessions() as s:
            assets = list(s.scalars(select(EcoAsset).order_by(EcoAsset.pole, EcoAsset.name)).all())
            checks = list(s.scalars(select(EcoCheck).where(EcoCheck.at >= since)
                                    .order_by(EcoCheck.at)).all())
            open_inc = list(s.scalars(select(EcoIncident).where(EcoIncident.status != "resolved")).all())
        by_asset: dict[int, list[EcoCheck]] = {}
        for c in checks:
            by_asset.setdefault(c.asset_id, []).append(c)
        inc_by: dict[int, list[EcoIncident]] = {}
        for i in open_inc:
            inc_by.setdefault(i.asset_id or 0, []).append(i)
        for a in assets:
            cs = by_asset.get(a.id, [])
            last = cs[-1] if cs else None
            worst = max((SEVERITY_RANK.get(i.severity, 0) for i in inc_by.get(a.id, [])
                         if i.status == "open" and i.severity != "info"), default=-1)
            if not a.active:
                state = "inactif"
            elif last is None:
                state = "inconnu"
            elif worst >= 2:
                state = "critique"
            elif worst == 1 or not last.ok:
                state = "attention"
            else:
                state = "ok"
            out_assets.append({
                "id": a.id, "name": a.name, "url": a.url, "kind": a.kind, "hosting": a.hosting,
                "pole": a.pole, "criticality": a.criticality, "active": a.active, "state": state,
                "status": last.status if last else None, "ms": last.ms if last else None,
                "at": as_utc(last.at).isoformat() if last else "",
                "uptime": round(100 * sum(c.ok for c in cs) / len(cs), 1) if cs else None,
                "spark": [c.ms if c.ok else -1 for c in cs[-24:]],
                "cert": last.cert_days if last else None, "notes": a.notes,
                "incidents": len([i for i in inc_by.get(a.id, []) if i.severity != "info"]),
            })
        active = [x for x in out_assets if x["active"]]
        crit = [x for x in active if x["state"] == "critique"]
        warn = [x for x in active if x["state"] == "attention"]
        certs = [x["cert"] for x in active if x["cert"] is not None]
        mss = [x["ms"] for x in active if x["ms"] and x["state"] == "ok"]
        ups = [x["uptime"] for x in active if x["uptime"] is not None]
        srv = [i for i in open_inc if i.asset_id is None and i.status == "open" and i.severity != "info"]
        srv_crit = [i for i in srv if i.severity == "critical"]
        if crit or srv_crit:
            parts = ([f"{len(crit)} actif(s) en panne ou en danger"] if crit else []) + \
                    ([f"{len(srv_crit)} problème(s) urgent(s) sur le serveur"] if srv_crit else [])
            level, message = "critique", " · ".join(parts)
        elif warn or srv:
            parts = ([f"{len(warn)} point(s) d'attention"] if warn else []) + \
                    ([f"{len(srv)} point(s) à voir sur le serveur"] if srv else [])
            level, message = "attention", " · ".join(parts)
        elif not active or all(x["state"] == "inconnu" for x in active):
            level, message = "inconnu", "Première mesure en cours…"
        else:
            level, message = "ok", "Tout est normal"
        improvements = [i for i in open_inc if i.severity == "info"]
        return {
            "level": level, "message": message, "updated": now.isoformat(),
            "assets": out_assets,
            "totals": {"suivis": len(active), "a_confirmer": len(out_assets) - len(active),
                       "ok": sum(x["state"] == "ok" for x in active), "attention": len(warn),
                       "critique": len(crit), "cert_min": min(certs) if certs else None,
                       "ms_moyen": int(sum(mss) / len(mss)) if mss else None,
                       "disponibilite": round(sum(ups) / len(ups), 2) if ups else None,
                       "ameliorations": len(improvements)},
        }

    def briefing(self, now: datetime | None = None) -> str:
        """Texte du « bonjour » quotidien : tout est normal, ou la liste des points à traiter."""
        data = self.summary(now)
        if data["level"] == "ok":
            t = data["totals"]
            return (f"Bonjour. Tout est normal : {t['suivis']} actifs suivis, disponibilité "
                    f"{t['disponibilite']} % sur 24 h, {t['ameliorations']} amélioration(s) possible(s).")
        lines = [f"Bonjour. {data['message']} :"]
        for i in self.incidents():
            if i.severity != "info" and i.status == "open":
                lines.append(f"- {i.title}")
        return "\n".join(lines)

    def purge(self, now: datetime | None = None) -> int:
        cutoff = (now or utcnow()) - timedelta(days=RETENTION_DAYS)
        with self._sessions() as s:
            n = s.execute(delete(EcoCheck).where(EcoCheck.at < cutoff)).rowcount
            s.commit()
        return n or 0
