"""Contrôles de données en lecture seule : refus de tout ce qui n'est pas un SELECT, secrets
chiffrés et jamais réaffichés, constats ouverts puis résolus."""

import pytest
from fastapi.testclient import TestClient

from ibig_agent.anomalies import AnomalyService
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import ServiceSetting
from ibig_agent.dbmonitor import DbMonitor, DbMonitorError, validate_sql

SECRET = "Mdp-Tres-Secret-42"


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, sql):
        self.db.executed.append(sql)

    def fetchone(self):
        if self.db.fail:
            raise RuntimeError(f"accès refusé pour {SECRET}")
        return (self.db.value,)

    def close(self):
        pass


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def rollback(self):
        self.db.rolled_back += 1

    def close(self):
        self.db.closed += 1


class FakeDb:
    def __init__(self):
        self.value, self.fail, self.executed, self.rolled_back, self.closed = 3, False, [], 0, 0

    def connect(self, conn):
        return FakeConn(self)


def monitor(rt, db):
    anomalies = AnomalyService(rt.sessions, rt.settings, rt.org, rt.kb, pause=0)
    return DbMonitor(rt.sessions, rt.settings, anomalies, connect=db.connect)


@pytest.mark.parametrize("sql", [
    "DELETE FROM clients", "SELECT 1; DROP TABLE clients", "UPDATE t SET a=1",
    "SELECT * INTO OUTFILE '/tmp/x' FROM t", "SELECT COUNT(*) FROM t -- fin",
    "SELECT SLEEP(10)", "INSERT INTO t VALUES (1)", "", "SHOW TABLES",
    "SELECT COUNT(*) FROM t /* x */", "WITH x AS (DELETE FROM t RETURNING 1) SELECT 1"])
def test_anything_but_a_plain_select_is_refused(sql):
    with pytest.raises(DbMonitorError):
        validate_sql(sql)


def test_a_plain_count_is_accepted_and_cleaned():
    assert validate_sql(" SELECT COUNT(*)\n FROM commandes WHERE statut = 'payee' ; ") == \
        "SELECT COUNT(*) FROM commandes WHERE statut = 'payee'"


def test_secrets_are_encrypted_never_listed_and_never_in_errors(rt):
    db = FakeDb()
    mon = monitor(rt, db)
    mon.add_connection("Scolaby", "mysql", "db.exemple.com", 0, "scolaby", "lecteur", SECRET, "Awa")
    with rt.sessions() as s:
        stored = s.get(ServiceSetting, "db_connections")
    assert stored.secret and SECRET not in stored.value and "db.exemple.com" not in stored.value
    assert mon.connection_names() == [("Scolaby", "MySQL / MariaDB",
                                       "db.exemple.com:3306/scolaby", "lecteur")]
    assert SECRET not in str(mon.connection_names())
    db.fail = True
    with pytest.raises(DbMonitorError) as err:
        mon.test("Scolaby")
    assert SECRET not in str(err.value) and "***" in str(err.value)
    with pytest.raises(DbMonitorError, match="obligatoires"):
        mon.add_connection("X", "mysql", "h", 0, "b", "u", "", "Awa")


def test_a_check_above_threshold_opens_a_finding_then_resolves_it(rt):
    db = FakeDb()
    mon = monitor(rt, db)
    mon.add_connection("Scolaby", "mysql", "h", 3306, "b", "u", SECRET, "Awa")
    with pytest.raises(DbMonitorError, match="inconnue"):
        mon.add_check("Autre", "x", "SELECT 1", 0, "warning", "Awa")
    cid = mon.add_check("Scolaby", "Paiements sans livraison",
                        "SELECT COUNT(*) FROM commandes WHERE payee = 1 AND livree = 0", 0,
                        "critical", "Awa")
    assert mon.run() == {"checks": 1, "anomalies": 1, "errors": 0}
    (f,) = mon.anomalies.findings()
    assert f.check == "donnees" and f.severity == "critical" and "(3)" in f.title
    assert mon.checks()[0].last_value == 3 and mon.checks()[0].id == cid
    assert db.executed[0].startswith("SET SESSION TRANSACTION READ ONLY")      # lecture seule
    assert "START TRANSACTION READ ONLY" in db.executed
    assert db.rolled_back == db.closed and db.closed >= 1                      # toujours annulé
    db.value = 0
    mon.run()
    assert mon.anomalies.findings()[0].status == "resolved"
    db.value = 5
    mon.run()
    assert mon.anomalies.findings()[0].status == "open"


def test_a_failing_check_is_reported_without_the_password_and_non_numbers_are_refused(rt):
    db = FakeDb()
    mon = monitor(rt, db)
    mon.add_connection("Scolaby", "postgresql", "h", 0, "b", "u", SECRET, "Awa")
    mon.add_check("Scolaby", "Comptes", "SELECT COUNT(*) FROM users", 0, "warning", "Awa")
    db.fail = True
    assert mon.run()["errors"] == 1
    (f,) = mon.anomalies.findings()
    assert "n'a pas pu s'exécuter" in f.title and SECRET not in f.detail
    assert "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY" in db.executed
    db.fail, db.value = False, "texte"
    assert mon.run()["errors"] == 1 and "un seul nombre" in mon.checks()[0].last_error


def test_removing_a_connection_removes_its_checks(rt):
    mon = monitor(rt, FakeDb())
    mon.add_connection("A", "mysql", "h", 1, "b", "u", SECRET, "Awa")
    mon.add_connection("B", "mysql", "h", 1, "b", "u", SECRET, "Awa")
    mon.add_check("A", "ca", "SELECT 1", 0, "warning", "Awa")
    mon.add_check("B", "cb", "SELECT 1", 0, "warning", "Awa")
    mon.remove_connection("A", "Awa")
    assert [c.label for c in mon.checks()] == ["cb"] and len(mon.connection_names()) == 1


def test_database_page_hides_passwords_and_is_closed_to_validators(rt, monkeypatch):
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("v@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    dg = client("dg@ibig.test")
    dg.post("/donnees/connexion", data={"nom": "Scolaby", "type": "mysql", "hote": "h.exemple.com",
                                         "base": "b", "utilisateur": "lecteur",
                                         "mot_de_passe": SECRET})
    text = dg.get("/donnees").text
    assert "Scolaby" in text and "lecteur" in text and SECRET not in text
    bad = dg.post("/donnees/controle", data={"connexion": "Scolaby", "nom": "x",
                                              "requete": "DELETE FROM clients"},
                  follow_redirects=False)
    assert "Refus" in bad.headers["location"] and rt.dbmonitor.checks() == []
    dg.post("/donnees/controle", data={"connexion": "Scolaby", "nom": "Doublons",
                                        "requete": "SELECT COUNT(*) FROM clients", "seuil": "0,5"})
    assert rt.dbmonitor.checks()[0].threshold == 0.5 and "Doublons" in dg.get("/donnees").text
    v = client("v@ibig.test")
    assert v.get("/donnees").status_code == 403
    assert v.post("/donnees/connexion", data={"nom": "x"}).status_code == 403


def test_ibig_core_catalog_is_valid_read_only_and_installs_without_duplicates(rt):
    from ibig_agent import core_checks
    for module, name, sql, _, severity in core_checks.CATALOG:
        assert validate_sql(sql).upper().startswith(("SELECT", "(SELECT")), name
        assert severity in ("warning", "critical") and len(f"{module} : {name}") <= 120
    mon = monitor(rt, FakeDb())
    mon.add_connection("IBIG CORE", "mysql", "h", 3306, "b", "u", SECRET, "dg")
    first = core_checks.install(mon, "IBIG CORE", "dg")
    assert first == {"added": len(core_checks.CATALOG), "skipped": 0}
    assert core_checks.install(mon, "IBIG CORE", "dg") == {"added": 0, "skipped": len(core_checks.CATALOG)}
    assert len(mon.checks()) == len(core_checks.CATALOG)
