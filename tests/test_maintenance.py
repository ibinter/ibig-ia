"""Console de maintenance : liste fermée, confirmation obligatoire, expiration, accès réservé."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import MaintenanceAction, utcnow
from ibig_agent.maintenance_catalog import CATALOG, command_for


def test_the_catalog_is_closed_and_commands_are_fixed():
    assert command_for("redemarrer", "ibig-digital") == ["pm2", "restart", "ibig-digital"]
    assert command_for("redemarrer", "santarex_backend") == ["docker", "restart", "santarex_backend"]
    for bad in [("redemarrer", "ibig-digital; rm -rf /"), ("redemarrer", "postgres"), ("effacer", "tout"),
                ("redemarrer", "ibig-ia-agent-1"), ("sauvegarder", "")]:
        assert command_for(*bad) is None
    # aucune base de données ni l'agent lui-même dans la liste
    names = {t for e in CATALOG.values() for t in e["targets"]}
    assert not any("postgres" in n or "redis" in n or "ibig-ia" in n for n in names)


def test_a_request_must_be_confirmed_and_cannot_be_duplicated(rt):
    s = rt.maintenance
    with pytest.raises(ValueError):
        s.request("redemarrer", "inconnu", "dg")
    mid = s.request("redemarrer", "ibig-digital", "dg")
    assert s.recent()[0].status == "demandee"
    with pytest.raises(ValueError):
        s.request("redemarrer", "ibig-digital", "dg")            # déjà en cours
    s.confirm(mid, "dg")
    assert s.recent()[0].status == "confirmee" and s.recent()[0].confirmed_by == "dg"
    with pytest.raises(ValueError):
        s.confirm(mid, "dg")                                     # déjà confirmée
    other = s.request("sauvegarder", "plateformes", "dg")
    s.cancel(other, "dg")
    assert [r.status for r in s.recent() if r.id == other] == ["annulee"]


def test_an_unconfirmed_request_expires_and_a_tampered_row_is_refused(rt):
    s = rt.maintenance
    mid = s.request("redemarrer", "docpro", "dg")
    with rt.sessions() as db:
        row = db.get(MaintenanceAction, mid)
        row.created_at = utcnow() - timedelta(hours=3)
        db.commit()
    with pytest.raises(ValueError):
        s.confirm(mid, "dg")
    assert s.recent()[0].status == "annulee"
    bad = s.request("redemarrer", "lokativo", "dg")
    with rt.sessions() as db:
        db.get(MaintenanceAction, bad).target = "ibig-digital; reboot"
        db.commit()
    with pytest.raises(ValueError):
        s.confirm(bad, "dg")
    with rt.sessions() as db:
        assert db.scalar(select(MaintenanceAction.status).where(MaintenanceAction.id == bad)) == "refusee"


def test_page_is_reserved_to_admin_and_direction(rt):
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("val@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")
    dg = TestClient(create_app(rt), base_url="https://testserver")
    assert dg.get("/maintenance", follow_redirects=False).status_code == 303
    dg.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    r = dg.post("/maintenance/demander", data={"action": "redemarrer", "target": "ibig-digital"},
                follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert "IBIG DIGITAL" in dg.get("/maintenance").text
    mid = rt.maintenance.recent()[0].id
    assert dg.post(f"/maintenance/{mid}/confirmer", follow_redirects=False).status_code == 303
    assert rt.maintenance.recent()[0].status == "confirmee"
    val = TestClient(create_app(rt), base_url="https://testserver")
    val.post("/login", data={"email": "val@ibig.test", "password": "mot-de-passe-solide"})
    assert val.get("/maintenance", follow_redirects=False).status_code in (403, 303)
    assert val.post("/maintenance/demander", data={"action": "redemarrer", "target": "docpro"},
                    follow_redirects=False).status_code in (403, 303)
    assert len(rt.maintenance.recent()) == 1                     # le valideur n'a rien pu demander
