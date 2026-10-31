"""Contrôles de données en LECTURE SEULE sur les bases des plateformes (MySQL ou PostgreSQL).

Principe de sécurité, appliqué à chaque requête :
  - les accès sont saisis dans l'application, chiffrés et jamais réaffichés ni journalisés ;
  - l'agent n'exécute QUE des contrôles écrits et approuvés par la direction : une seule requête
    SELECT qui renvoie un nombre (par exemple un COUNT). Tout le reste est refusé avant l'envoi ;
  - la connexion est ouverte en lecture seule, avec un délai maximal court, puis annulée (rollback) ;
  - un contrôle est « anormal » quand son nombre dépasse le seuil fixé (0 par défaut). Le résultat
    devient un constat sur la page Anomalies : l'agent signale, il ne corrige jamais rien.
Recommandation : créer chez chaque hébergeur un utilisateur qui n'a que le droit SELECT.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from .anomalies import AnomalyService, Finding
from .config import Settings
from .configstore import service_value, set_service_value
from .db import utcnow

CONN_KEY = "db_connections"      # JSON chiffré (contient les mots de passe)
CHECKS_KEY = "db_checks"
KINDS = {"mysql": "MySQL / MariaDB", "postgresql": "PostgreSQL"}
DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432}
TIMEOUT_S = 8
MAX_SQL = 1500
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|replace|merge|call|exec|"
    r"execute|into|outfile|dumpfile|load_file|load|sleep|benchmark|pg_sleep|lock|set|handler|do|"
    r"copy|vacuum|analyze|prepare|declare)\b", re.IGNORECASE)


class DbMonitorError(ValueError):
    pass


@dataclass
class Connection:
    name: str
    kind: str
    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass
class DbCheck:
    id: int
    connection: str
    label: str
    sql: str
    threshold: float = 0.0
    severity: str = "warning"
    last_value: float | None = None
    last_run: str = ""
    last_error: str = ""


def validate_sql(sql: str) -> str:
    """Accepte une seule requête SELECT sans commentaire ni mot d'écriture ; sinon lève l'erreur."""
    text = " ".join(sql.strip().rstrip(";").split())
    if not text or len(text) > MAX_SQL:
        raise DbMonitorError(f"Requête vide ou trop longue ({MAX_SQL} caractères au plus)")
    if ";" in text:
        raise DbMonitorError("Une seule requête à la fois (pas de « ; »)")
    if "--" in text or "/*" in text or "#" in text:
        raise DbMonitorError("Pas de commentaire dans la requête")
    if not re.match(r"(?is)^\(?\s*select\b", text):
        raise DbMonitorError("Seule une requête SELECT est autorisée")
    bad = FORBIDDEN.search(text)
    if bad:
        raise DbMonitorError(f"Mot interdit dans un contrôle en lecture seule : « {bad.group(0)} »")
    return text


def _connect(c: Connection):
    """Ouvre une connexion (pilotes chargés à la demande)."""
    if c.kind == "mysql":
        import pymysql

        return pymysql.connect(host=c.host, port=c.port, user=c.user, password=c.password,
                               database=c.database, connect_timeout=TIMEOUT_S,
                               read_timeout=TIMEOUT_S, write_timeout=TIMEOUT_S, autocommit=False)
    import psycopg

    return psycopg.connect(host=c.host, port=c.port, user=c.user, password=c.password,
                           dbname=c.database, connect_timeout=TIMEOUT_S)


def _read_only(c: Connection, conn) -> None:
    cur = conn.cursor()
    if c.kind == "mysql":
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME={TIMEOUT_S * 1000}")
        cur.execute("START TRANSACTION READ ONLY")
    else:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        cur.execute(f"SET statement_timeout = {TIMEOUT_S * 1000}")
    cur.close()


class DbMonitor:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings,
                 anomalies: AnomalyService,
                 connect: Callable[[Connection], object] = _connect) -> None:
        self._sessions = sessions
        self.settings = settings
        self.anomalies = anomalies
        self._connect = connect

    # ------------------------------------------------------------------ connexions
    def _connections(self) -> list[Connection]:
        raw = service_value(self._sessions, self.settings, CONN_KEY)
        try:
            return [Connection(**i) for i in (json.loads(raw) if raw else [])]
        except (ValueError, TypeError):
            return []

    def connection_names(self) -> list[tuple[str, str, str, str]]:
        """(nom, type, adresse, utilisateur) : jamais le mot de passe."""
        return [(c.name, KINDS.get(c.kind, c.kind), f"{c.host}:{c.port}/{c.database}", c.user)
                for c in self._connections()]

    def add_connection(self, name: str, kind: str, host: str, port: int, database: str,
                       user: str, password: str, by: str) -> None:
        name = " ".join(name.split())[:60]
        if kind not in KINDS:
            raise DbMonitorError("Type de base inconnu")
        if not (name and host.strip() and database.strip() and user.strip() and password):
            raise DbMonitorError("Nom, hôte, base, utilisateur et mot de passe sont obligatoires")
        items = [c for c in self._connections() if c.name != name]
        items.append(Connection(name, kind, host.strip(), port or DEFAULT_PORTS[kind],
                                database.strip(), user.strip(), password))
        set_service_value(self._sessions, self.settings, CONN_KEY,
                          json.dumps([asdict(c) for c in items]), by, secret=True)

    def remove_connection(self, name: str, by: str) -> None:
        items = [c for c in self._connections() if c.name != name]
        set_service_value(self._sessions, self.settings, CONN_KEY,
                          json.dumps([asdict(c) for c in items]), by, secret=True)
        self._save_checks([k for k in self.checks() if k.connection != name], by)

    def _get(self, name: str) -> Connection:
        for c in self._connections():
            if c.name == name:
                return c
        raise DbMonitorError("Connexion inconnue")

    def _scrub(self, text: str) -> str:
        for c in self._connections():
            if c.password:
                text = text.replace(c.password, "***")
        return text[:200]

    def test(self, name: str) -> str:
        """Ouvre la connexion et lit « 1 » : dit si l'accès fonctionne, sans rien lire d'autre."""
        c = self._get(name)
        try:
            conn = self._connect(c)
            try:
                _read_only(c, conn)
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                conn.rollback()
                conn.close()
        except Exception as exc:
            raise DbMonitorError(f"Connexion impossible : {self._scrub(str(exc))}") from exc
        return "Connexion réussie (lecture seule)"

    # ------------------------------------------------------------------ contrôles
    def checks(self) -> list[DbCheck]:
        raw = service_value(self._sessions, self.settings, CHECKS_KEY)
        try:
            return [DbCheck(**i) for i in (json.loads(raw) if raw else [])]
        except (ValueError, TypeError):
            return []

    def _save_checks(self, items: list[DbCheck], by: str) -> None:
        set_service_value(self._sessions, self.settings, CHECKS_KEY,
                          json.dumps([asdict(i) for i in items]), by)

    def add_check(self, connection: str, label: str, sql: str, threshold: float,
                  severity: str, by: str) -> int:
        self._get(connection)
        label = " ".join(label.split())[:120]
        if not label:
            raise DbMonitorError("Donnez un nom au contrôle")
        if severity not in ("warning", "critical"):
            raise DbMonitorError("Gravité inconnue")
        clean = validate_sql(sql)
        items = self.checks()
        cid = max((i.id for i in items), default=0) + 1
        items.append(DbCheck(cid, connection, label, clean, threshold, severity))
        self._save_checks(items, by)
        return cid

    def remove_check(self, cid: int, by: str) -> None:
        self._save_checks([i for i in self.checks() if i.id != cid], by)

    # ------------------------------------------------------------------ exécution
    def _value(self, c: Connection, sql: str) -> float:
        conn = self._connect(c)
        try:
            _read_only(c, conn)
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        finally:
            conn.rollback()
            conn.close()
        if not row or row[0] is None or isinstance(row[0], (str, bytes)):
            raise DbMonitorError("La requête doit renvoyer un seul nombre (par exemple un COUNT)")
        return float(row[0])

    def run(self, now: datetime | None = None) -> dict:
        """Exécute tous les contrôles et met à jour les constats (ouverts, rouverts, résolus)."""
        now = now or utcnow()
        checks = self.checks()
        findings = self.anomalies.findings()
        new_critical: list[Finding] = []
        out = {"checks": len(checks), "anomalies": 0, "errors": 0}
        for k in checks:
            produced: set[str] = set()
            target = f"Base {k.connection}"
            try:
                value = self._value(self._get(k.connection), validate_sql(k.sql))
                k.last_value, k.last_error = value, ""
                if value > k.threshold:
                    out["anomalies"] += 1
                    f = Finding(f"db|{k.connection}|{k.id}", "donnees", k.severity, target,
                                f"{k.connection} : {k.label} ({value:g})",
                                f"Le contrôle « {k.label} » renvoie {value:g} "
                                f"(seuil toléré : {k.threshold:g}).")
                    produced.add(f.key)
                    if self.anomalies._upsert(findings, f, now) and k.severity == "critical":
                        new_critical.append(f)
            except Exception as exc:  # noqa: BLE001 — erreur affichée sans mot de passe
                out["errors"] += 1
                k.last_error = self._scrub(str(exc))
                f = Finding(f"db|{k.connection}|{k.id}|erreur", "donnees", "warning", target,
                            f"{k.connection} : le contrôle « {k.label} » n'a pas pu s'exécuter",
                            k.last_error)
                produced.add(f.key)
                self.anomalies._upsert(findings, f, now)
            k.last_run = now.strftime("%Y-%m-%d %H:%M")
            mine = {f"db|{k.connection}|{k.id}", f"db|{k.connection}|{k.id}|erreur"}
            for f in findings:
                if f.check == "donnees" and f.key in mine \
                        and f.key not in produced and f.status != "resolved":
                    f.status = "resolved"
        self.anomalies._save(findings)
        self.anomalies._notify(new_critical)
        self._save_checks(checks, "contrôle automatique")
        return out
