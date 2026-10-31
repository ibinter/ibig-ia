"""Surveillance des sites et produits, bugs signalés et incohérences (lecture seule).

Ce que fait ce module, et seulement cela :
  - il LIT les pages publiques des sites des pôles et des produits (adresses du catalogue),
    avec un seul accès à la fois et une pause entre deux requêtes ;
  - il contrôle : le site répond, sa vitesse, la validité du certificat, les liens cassés de la page
    d'accueil, les adresses mail et téléphones absents de la base de connaissances ;
  - il regroupe les bugs que les clients signalent (tickets et mails) par produit ;
  - il tient une liste de constats (ouvert, pris en compte, résolu) et alerte la direction à
    l'apparition d'un constat critique.
Il n'accède à aucune base de données ni à aucun code, et ne modifie rien chez les produits.
Les constats sont stockés dans les réglages (clé `anomaly_findings`, 300 au plus) : aucune migration.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import OrgConfig, Settings
from .configstore import service_value, set_service_value
from .db import ProcessedMessage, Ticket, utcnow
from .knowledge import _EMAIL, _PHONE, KnowledgeBase, _digits

KEY = "anomaly_findings"
URLS_KEY = "monitor_urls"
MAX_FINDINGS = 300
SLOW_SECONDS = 5.0
SSL_WARN_DAYS = 14
SSL_CRITICAL_DAYS = 3
LINK_SAMPLE = 12
BUG_WINDOW_DAYS = 30
SEVERITIES = {"info": 0, "warning": 1, "critical": 2}
SEVERITY_LABELS = {"info": "À savoir", "warning": "À corriger", "critical": "Urgent"}
CHECK_LABELS = {"dispo": "Disponibilité", "vitesse": "Vitesse", "ssl": "Certificat",
                "liens": "Liens cassés", "contacts": "Contacts", "bug": "Bug signalé",
                "donnees": "Données", "sauvegarde": "Sauvegarde"}
LIGHT_CHECKS = ("dispo", "vitesse", "ssl")
DEEP_CHECKS = (*LIGHT_CHECKS, "liens", "contacts")
BUG_WORDS = re.compile(
    r"ne (fonctionne|marche|charge|r[ée]pond|s'affiche)\s+(pas|plus)|\bbug\b|\berreur\b|plant[ée]|"
    r"bloqu[ée]|impossible de|page blanche|[ée]cran noir|\bcrash|donn[ée]es? (perdues?|disparu)|"
    r"tr[èe]s lent|inaccessible|ne s'ouvre pas", re.IGNORECASE)
HREF = re.compile(r"""href\s*=\s*["']([^"'#][^"']*)["']""", re.IGNORECASE)
TABLE_ROW = re.compile(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*(https?://[^\s|]+)\s*\|", re.MULTILINE)


CONTACT_CAP = 5
HIDDEN = re.compile(r"(?is)<(script|style|svg|noscript)\b.*?</\1>|<!--.*?-->")
TAGS = re.compile(r"(?s)<[^>]*>")
MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w.])(?:\+|00)?\d[\d ]{7,17}\d(?![\w.])")
HREF_CONTACT = re.compile(r"""href\s*=\s*["'](mailto|tel):([^"'?]+)""", re.IGNORECASE)


def page_contacts(html: str, host: str) -> tuple[set[str], dict[str, str]]:
    """Adresses mail et téléphones LISIBLES d'une page : le texte visible et les liens mailto: et
    tel:, sans scripts, styles, dessins (SVG) ni balises. Évite de prendre des données graphiques
    pour des numéros et des noms de bibliothèques pour des adresses. Seules les adresses des
    domaines du groupe (ou du site lui-même) sont retenues."""
    contact_links = HREF_CONTACT.findall(html)
    text = TAGS.sub(" ", HIDDEN.sub(" ", html))
    base = ".".join(host.split(".")[-2:])
    mails = {m.lower() for m in MAIL_RE.findall(text)}
    mails |= {v.strip().lower() for kind, v in contact_links if kind.lower() == "mailto"}
    mails = {m for m in mails if MAIL_RE.fullmatch(m)
             and (m.endswith(("@" + base, "." + base))
                  or any(k in m.split("@")[1] for k in ("ibig", "intermark")))}
    phones: dict[str, str] = {}
    candidates = PHONE_RE.findall(text)
    candidates += [v for kind, v in contact_links if kind.lower() == "tel"]
    for raw in candidates:
        digits = _digits(raw)
        if 9 <= len(digits) <= 13:
            phones.setdefault(digits, raw.strip())
    return mails, phones


@dataclass
class Target:
    name: str
    url: str
    kind: str       # site | produit | ajoute
    pole: str = ""


@dataclass
class Finding:
    key: str
    check: str
    severity: str
    target: str
    title: str
    detail: str = ""
    status: str = "open"          # open | ack | resolved
    count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    by: str = ""
    extra: dict = field(default_factory=dict)


def _now_iso(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M")


def cert_days(host: str, timeout: float = 6.0) -> int | None:
    """Jours restants avant l'expiration du certificat du site (None si illisible)."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
        end = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=UTC)
        return (end - utcnow()).days
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


def open_findings(s: Session) -> int:
    """Constats ouverts (hors « à savoir ») : pastille du menu, sans construire le service."""
    from .db import ServiceSetting

    row = s.get(ServiceSetting, KEY)
    try:
        items = json.loads(row.value) if row and row.value else []
    except ValueError:
        return 0
    return sum(i.get("status") == "open" and i.get("severity") != "info" for i in items)


class AnomalyService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, org: OrgConfig,
                 kb: KnowledgeBase, alert: Callable[[str, str, str], None] | None = None,
                 client: httpx.Client | None = None,
                 cert_check: Callable[[str], int | None] = cert_days, pause: float = 0.3) -> None:
        self._sessions = sessions
        self.settings = settings
        self.org = org
        self.kb = kb
        self.alert = alert
        self._client = client
        self.cert_check = cert_check
        self.pause = pause

    # ------------------------------------------------------------------ cibles
    def targets(self) -> list[Target]:
        out: dict[str, Target] = {}
        for p in self.org.poles:
            if p.site:
                out.setdefault(p.site.rstrip("/"), Target(f"Site {p.code}", p.site, "site", p.code))
        for doc in self.kb.documents:
            if doc.type != "catalogue":
                continue
            for name, url in TABLE_ROW.findall(doc.body):
                out.setdefault(url.rstrip("/"), Target(name, url, "produit", doc.pole))
        for line in (service_value(self._sessions, self.settings, URLS_KEY) or "").splitlines():
            url = line.strip()
            if url.startswith(("http://", "https://")):
                out.setdefault(url.rstrip("/"), Target(urlparse(url).netloc, url, "ajoute"))
        return list(out.values())

    def extra_urls(self) -> str:
        return service_value(self._sessions, self.settings, URLS_KEY)

    def set_extra_urls(self, text: str, by: str) -> int:
        urls = [u.strip() for u in text.splitlines() if u.strip().startswith(("http://", "https://"))]
        set_service_value(self._sessions, self.settings, URLS_KEY, "\n".join(urls[:50]), by)
        return len(urls[:50])

    # ------------------------------------------------------------------ constats
    def findings(self) -> list[Finding]:
        raw = service_value(self._sessions, self.settings, KEY)
        try:
            return [Finding(**i) for i in (json.loads(raw) if raw else [])]
        except (ValueError, TypeError):
            return []

    def _save(self, items: list[Finding], by: str = "surveillance") -> None:
        items.sort(key=lambda f: (f.status == "resolved", -SEVERITIES.get(f.severity, 0),
                                  f.last_seen))
        live = [f for f in items if f.status != "resolved"]
        done = [f for f in items if f.status == "resolved"][:max(MAX_FINDINGS - len(live), 0)]
        set_service_value(self._sessions, self.settings, KEY,
                          json.dumps([asdict(f) for f in [*live, *done]]), by)

    def open_count(self) -> int:
        return sum(f.status == "open" and f.severity != "info" for f in self.findings())

    def set_status(self, key: str, status: str, by: str) -> None:
        if status not in ("open", "ack", "resolved"):
            raise ValueError("Statut inconnu")
        items = self.findings()
        for f in items:
            if f.key == key:
                f.status, f.by = status, by
                self._save(items, by)
                return
        raise ValueError("Constat introuvable")

    def _upsert(self, items: list[Finding], new: Finding, now: datetime) -> bool:
        """Ajoute ou met à jour un constat ; vrai s'il est nouveau ou rouvert."""
        stamp = _now_iso(now)
        for f in items:
            if f.key != new.key:
                continue
            reopened = f.status == "resolved"
            worse = SEVERITIES.get(new.severity, 0) > SEVERITIES.get(f.severity, 0)
            f.title, f.detail = new.title, new.detail
            f.severity = new.severity
            f.last_seen = stamp
            if reopened:
                f.status, f.count, f.first_seen, f.by = "open", 1, stamp, ""
            else:
                f.count += 1
            return reopened or worse      # aussi quand le problème s'aggrave (à corriger → urgent)
        new.first_seen = new.last_seen = stamp
        items.append(new)
        return True

    def _settle(self, items: list[Finding], target: str, covered: tuple[str, ...],
                produced: set[str]) -> None:
        """Marque résolus les constats ouverts d'une cible que ce passage ne retrouve plus."""
        for f in items:
            if f.target == target and f.check in covered and f.key not in produced \
                    and f.status != "resolved":
                f.status = "resolved"

    # ------------------------------------------------------------------ lecture des sites
    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(
            timeout=10, follow_redirects=True,
            headers={"User-Agent": "IBIG-Surveillance/1.0 (lecture seule)"})

    def check(self, t: Target, deep: bool, client: httpx.Client) -> list[Finding]:
        found: list[Finding] = []
        host = urlparse(t.url).netloc

        def add(check: str, sev: str, title: str, detail: str = "") -> None:
            found.append(Finding(f"{t.url}|{check}", check, sev, t.url,
                                 f"{t.name} : {title}", detail))

        start = time.monotonic()
        html = ""
        try:
            resp = client.get(t.url)
            elapsed = time.monotonic() - start
            if resp.status_code >= 400:
                add("dispo", "critical", f"répond en erreur {resp.status_code}",
                    f"La page d'accueil {t.url} renvoie le code {resp.status_code}.")
            else:
                html = resp.text
                if elapsed > SLOW_SECONDS:
                    add("vitesse", "warning", f"lent ({elapsed:.1f} s)",
                        f"La page d'accueil met {elapsed:.1f} s à répondre (seuil {SLOW_SECONDS:g} s).")
        except httpx.HTTPError as exc:
            add("dispo", "critical", "injoignable",
                f"{t.url} ne répond pas : {exc.__class__.__name__}.")
        days = self.cert_check(host) if t.url.startswith("https://") else None
        if days is not None and days < SSL_WARN_DAYS:
            add("ssl", "critical" if days < SSL_CRITICAL_DAYS else "warning",
                f"certificat expire dans {days} jour(s)" if days >= 0 else "certificat expiré",
                "Renouveler le certificat avant l'expiration.")
        if deep and html:
            found.extend(self._deep(t, html, client))
        return found

    def _deep(self, t: Target, html: str, client: httpx.Client) -> list[Finding]:
        found: list[Finding] = []
        host = urlparse(t.url).netloc
        links: list[str] = []
        for href in HREF.findall(html):
            if href.startswith(("mailto:", "tel:", "javascript:", "whatsapp:")):
                continue
            full = urljoin(t.url, href).split("#")[0]
            if urlparse(full).netloc == host and full not in links and full != t.url:
                links.append(full)
        for link in links[:LINK_SAMPLE]:
            time.sleep(self.pause)
            try:
                code = client.get(link).status_code
            except httpx.HTTPError:
                code = 0
            if code == 0 or code >= 400:
                found.append(Finding(f"{t.url}|liens|{link}", "liens", "warning", t.url,
                                     f"{t.name} : lien cassé",
                                     f"{link} renvoie {code or 'aucune réponse'}."))
        known_mails, known_phones = self._known_contacts()
        mails, phones = page_contacts(html, host)
        for mail in sorted(mails - known_mails)[:CONTACT_CAP]:
            found.append(Finding(f"{t.url}|contacts|{mail}", "contacts", "info", t.url,
                                 f"{t.name} : adresse absente de la base",
                                 f"{mail} figure sur le site mais pas dans la base de "
                                 "connaissances : l'agent ne l'utilisera pas."))
        for digits, shown in sorted(phones.items()):
            if digits not in known_phones and len(found) < 4 * CONTACT_CAP:
                found.append(Finding(f"{t.url}|contacts|{digits}", "contacts", "info", t.url,
                                     f"{t.name} : téléphone absent de la base",
                                     f"{shown} figure sur le site mais pas dans la base."))
        return found

    def _known_contacts(self) -> tuple[set[str], set[str]]:
        mails: set[str] = set()
        phones: set[str] = set()
        for d in self.kb.documents:
            mails.update(m.lower() for m in _EMAIL.findall(d.body))
            phones.update(_digits(p) for p in _PHONE.findall(d.body))
        return mails, phones

    def scan_sites(self, now: datetime | None = None, deep: bool = False) -> dict:
        now = now or utcnow()
        covered = DEEP_CHECKS if deep else LIGHT_CHECKS
        items = self.findings()
        new_critical: list[Finding] = []
        client = self._http()
        n = 0
        try:
            for t in self.targets():
                n += 1
                produced: set[str] = set()
                for f in self.check(t, deep, client):
                    produced.add(f.key)
                    if self._upsert(items, f, now) and f.severity == "critical":
                        new_critical.append(f)
                self._settle(items, t.url, covered, produced)
                time.sleep(self.pause)
        finally:
            if self._client is None:
                client.close()
        self._save(items)
        self._notify(new_critical)
        return {"checked": n, "open": self.open_count(), "critical": len(new_critical)}

    # ------------------------------------------------------------------ bugs signalés
    def _products(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for doc in self.kb.documents:
            if doc.type == "catalogue":
                for name, _ in TABLE_ROW.findall(doc.body):
                    out[name.lower()] = doc.pole
        return out

    def scan_bugs(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        since = now - timedelta(days=BUG_WINDOW_DAYS)
        products = self._products()
        signals: dict[str, list[str]] = {}

        def group(text: str, pole: str) -> str:
            low = text.lower()
            for name in products:
                if name in low:
                    return name.upper()
            return f"Pôle {pole}" if pole else "Produit non précisé"

        with self._sessions() as s:
            for t in s.scalars(select(Ticket).where(Ticket.created_at >= since)).all():
                text = f"{t.question} {t.reason}"
                if BUG_WORDS.search(text):
                    signals.setdefault(group(text, t.pole), []).append(
                        f"ticket n° {t.id} : {' '.join(t.question.split())[:110]}")
            for m in s.scalars(select(ProcessedMessage).where(
                    ProcessedMessage.processed_at >= since,
                    ProcessedMessage.suspicious.is_(False))).all():
                text = f"{m.subject} {m.summary}"
                if m.category != "spam" and BUG_WORDS.search(text):
                    signals.setdefault(group(text, m.pole), []).append(
                        f"mail : {' '.join(m.subject.split())[:110]}")
        items = self.findings()
        new_critical: list[Finding] = []
        produced: set[str] = set()
        for product, rows in signals.items():
            n = len(rows)
            sev = "critical" if n >= 5 else "warning" if n >= 2 else "info"
            f = Finding(f"bug|{product}", "bug", sev, product,
                        f"{product} : {n} signalement{'s' if n > 1 else ''} de bug en "
                        f"{BUG_WINDOW_DAYS} jours",
                        "\n".join(rows[:6]) + (f"\n… et {n - 6} autres" if n > 6 else ""))
            produced.add(f.key)
            if self._upsert(items, f, now) and sev == "critical":
                new_critical.append(f)
        for f in items:                       # plus aucun signalement récent : résolu
            if f.check == "bug" and f.key not in produced and f.status != "resolved":
                f.status = "resolved"
        self._save(items)
        self._notify(new_critical)
        return {"products": len(signals), "signals": sum(len(v) for v in signals.values())}

    # ------------------------------------------------------------------ sauvegardes
    def scan_platform_backup(self, now: datetime | None = None,
                             state_dir: Path | None = None) -> dict:
        """Surveille la sauvegarde nocturne des bases de TOUTES les plateformes
        (deploy/sauvegarde-plateformes.sh, qui écrit state/sauvegarde-plateformes.json) : absente,
        en échec, partielle ou trop ancienne, c'est un constat. Aucune sauvegarde n'est lue."""
        now = now or utcnow()
        path = Path(state_dir or self.settings.state_dir) / "sauvegarde-plateformes.json"
        problem: tuple[str, str, str] | None = None        # gravité, titre, détail
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            when = datetime.fromisoformat(str(raw.get("date", "")))
            when = when if when.tzinfo else when.replace(tzinfo=UTC)
            age = (now - when).total_seconds() / 3600
            message = str(raw.get("message", "")).strip()
            if raw.get("etat") == "echec":
                problem = ("critical", "dernière sauvegarde des plateformes en échec", message)
            elif age > 36:
                detail = (f"Dernière : {when:%d/%m/%Y %H:%M}. Contrôler la tâche planifiée du "
                          "serveur (cron de 2 h 45).")
                problem = ("critical", f"aucune sauvegarde des plateformes depuis {age:.0f} h",
                           detail)
            elif raw.get("etat") == "partiel":
                problem = ("warning", "sauvegarde des plateformes incomplète", message)
        except (OSError, ValueError):
            problem = ("warning", "aucune sauvegarde des plateformes enregistrée",
                       "Programmer deploy/sauvegarde-plateformes.sh sur le serveur.")
        items = self.findings()
        key = "backup|plateformes"
        new_critical: list[Finding] = []
        if problem:
            f = Finding(key, "sauvegarde", problem[0], "Sauvegardes", f"Sauvegardes : {problem[1]}",
                        problem[2])
            if self._upsert(items, f, now) and problem[0] == "critical":
                new_critical.append(f)
        else:
            for f in items:
                if f.key == key and f.status != "resolved":
                    f.status = "resolved"
        self._save(items)
        self._notify(new_critical)
        return {"backup_ok": problem is None}

    # ------------------------------------------------------------------ passage complet
    def run(self, now: datetime | None = None) -> dict:
        """Contrôle léger toutes les heures ; contrôle complet (liens, contacts) 4 fois par jour."""
        now = now or utcnow()
        sites = self.scan_sites(now, deep=now.hour % 6 == 0)
        bugs = self.scan_bugs(now)
        backup = self.scan_platform_backup(now)
        return {**sites, **bugs, **backup}

    def _notify(self, critical: list[Finding]) -> None:
        if not critical or self.alert is None:
            return
        body = "\n\n".join(f"- {f.title}\n  {f.detail.splitlines()[0] if f.detail else ''}"
                           for f in critical[:8])
        self.alert("anomalie", f"[IBIG] {len(critical)} anomalie(s) urgente(s) détectée(s)",
                   body + "\n\nDétail et suivi : page Anomalies du tableau de bord.")
