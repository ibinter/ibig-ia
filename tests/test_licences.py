"""Licences et abonnements : échéances, renouvellement, statuts, page réservée à la direction."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.licences import add_months

TODAY = date(2026, 11, 5)


def test_add_months_keeps_the_last_day_of_short_months():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 5), 12) == date(2027, 11, 5)


def test_end_date_is_computed_states_and_summary(rt):
    s = rt.licences
    s.add("Clinique A", "SANTAREX", "annuel", 600000, date(2026, 1, 10))                 # fin 2027-01-10
    s.add("École B", "Scolaby", "mensuel", 30000, date(2026, 10, 20))                    # fin 2026-11-20 : bientôt
    s.add("Hôtel C", "IBIG SOFT", "annuel", 120000, date(2025, 3, 1))                    # fin 2026-03-01 : expirée
    s.add("Mairie D", "Site web", "unique", 250000, date(2026, 5, 1))                    # sans échéance
    by = {r["row"].client: r for r in s.rows(TODAY)}
    assert by["Clinique A"]["state"] == "active" and by["Clinique A"]["row"].ends_on == date(2027, 1, 10)
    assert by["École B"]["state"] == "bientot" and by["École B"]["days"] == 15
    assert by["Hôtel C"]["state"] == "expiree"
    assert by["Mairie D"]["state"] == "sans_echeance"
    sm = s.summary(TODAY)
    assert (sm["actives"], sm["bientot"], sm["expirees"]) == (3, 1, 1)
    assert sm["mensuel_fcfa"] == 600000 // 12 + 30000


def test_renewal_extends_from_the_end_or_today_and_reactivates(rt):
    s = rt.licences
    a = s.add("Client", "Produit", "annuel", 100000, date(2025, 1, 1))                   # expirée depuis 2026-01-01
    assert s.renew(a, TODAY) == date(2027, 11, 5)                                         # repart d'aujourd'hui
    b = s.add("Client", "Produit", "mensuel", 10000, date(2026, 10, 25))                  # fin 2026-11-25
    assert s.renew(b, TODAY) == date(2026, 12, 25)                                        # repart de l'échéance
    s.set_status(b, "suspendue")
    s.renew(b, TODAY)
    assert {r["row"].id: r["row"].status for r in s.rows(TODAY)}[b] == "active"
    u = s.add("Client", "Produit", "unique", 1, date(2026, 1, 1))
    with pytest.raises(ValueError):
        s.renew(u, TODAY)


def test_bad_input_is_refused(rt):
    s = rt.licences
    for args in [("", "P", "annuel", 1, TODAY), ("C", "", "annuel", 1, TODAY),
                 ("C", "P", "inconnu", 1, TODAY), ("C", "P", "annuel", -5, TODAY)]:
        with pytest.raises(ValueError):
            s.add(*args)
    with pytest.raises(ValueError):
        s.add("C", "P", "annuel", 1, date(2026, 5, 1), ends_on=date(2026, 4, 1))
    with pytest.raises(ValueError):
        s.set_status(999, "active")


def test_page_is_for_direction_and_actions_work(rt):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    assert c.get("/licences", follow_redirects=False).status_code == 303
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    r = c.post("/licences", data={"client": "Clinique Z", "product": "SANTAREX", "plan": "annuel",
                                  "amount": "500000", "starts_on": "2026-11-01"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Clinique Z" in c.get("/licences").text
    lid = rt.licences.rows()[0]["row"].id
    assert c.post(f"/licences/{lid}/renouveler", follow_redirects=False).status_code == 303
    assert c.post(f"/licences/{lid}/statut", data={"statut": "suspendue"}, follow_redirects=False).status_code == 303
    assert rt.licences.rows()[0]["state"] == "suspendue"
    bad = c.post("/licences", data={"client": "", "product": "x"}, follow_redirects=False)
    assert bad.status_code == 303 and "msg=" in bad.headers["location"]
