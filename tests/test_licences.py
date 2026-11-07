"""Licences et abonnements : échéances, renouvellement, statuts, page réservée à la direction."""

from datetime import UTC, date, datetime

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


RAW = [
    {"external_id": "1", "client": "Clinique A", "product": "SANTAREX", "raw_plan": "cabinet",
     "raw_status": "active", "amount": 300000, "starts": "2026-01-10", "ends": "2027-01-10"},
    {"external_id": "2", "client": "Cabinet B", "product": "SANTAREX", "raw_plan": "decouverte",
     "raw_status": "decouverte", "amount": 0, "starts": "2026-05-01", "ends": "2026-06-01"},
    {"external_id": "3", "client": "Dr C", "product": "SANTAREX", "raw_plan": "cabinet", "raw_status": "essai",
     "starts": "2026-10-20", "ends": "2026-11-20"},
    {"external_id": "4", "client": "Démo", "product": "SANTAREX", "raw_plan": "pro", "raw_status": "DEMO"},
    {"external_id": "5", "client": "École D", "product": "SCOLABY", "raw_plan": "PRO", "raw_status": "ACTIVE",
     "billing": "TRIMESTRIEL", "amount": "45000.0", "starts": "2026-09-01", "ends": "2026-12-01"},
    {"external_id": "", "client": "Sans identifiant", "product": "X"},
]


def test_import_normalizes_skips_demo_and_never_duplicates(rt):
    s = rt.licences
    res = s.import_rows("SANTAREX", RAW[:4] + RAW[5:])
    assert res == {"source": "SANTAREX", "created": 3, "updated": 0, "skipped": 2}
    by = {r["row"].client: r["row"] for r in s.rows(TODAY)}
    assert by["Clinique A"].plan == "annuel" and by["Clinique A"].amount_fcfa == 300000
    assert by["Cabinet B"].plan == "gratuit" and by["Cabinet B"].ends_on is None
    assert by["Dr C"].plan == "essai" and by["Dr C"].source == "SANTAREX"
    s.import_rows("SCOLABY", [RAW[4]])
    assert {r["row"].client: r["row"].plan for r in s.rows(TODAY)}["École D"] == "trimestriel"
    again = s.import_rows("SANTAREX", RAW[:3])                       # relance : aucun doublon, rien à changer
    assert again["created"] == 0 and again["updated"] == 0
    changed = dict(RAW[0], ends="2028-01-10")
    assert s.import_rows("SANTAREX", [changed])["updated"] == 1
    assert sum(1 for r in s.rows(TODAY) if r["row"].client == "Clinique A") == 1
    s.add("Mairie", "Site web", "unique", 1000, date(2026, 1, 1))     # une saisie manuelle n'est jamais touchée
    s.import_rows("SANTAREX", [])
    assert any(r["row"].client == "Mairie" for r in s.rows(TODAY))
    summary = s.summary(TODAY)
    assert summary["gratuites"] == 1 and summary["essais"] == 1


def test_sync_file_reads_collector_output_and_reports_a_missing_file(rt, tmp_path):
    import json

    s = rt.licences
    with pytest.raises(ValueError):
        s.sync_file(tmp_path / "absent.json")
    f = tmp_path / "licences-collectees.json"
    f.write_text(json.dumps({"at": "2026-11-05T03:40:00", "sources": {"SANTAREX": RAW[:3], "SCOLABY": [RAW[4]]},
                             "errors": {}}), encoding="utf-8")
    out = s.sync_file(f)
    assert {r["source"]: r["created"] for r in out} == {"SANTAREX": 3, "SCOLABY": 1}


def test_ma_journee_warns_about_paid_licences_only(rt):
    from datetime import timedelta

    today = datetime.now(UTC).date()
    s = rt.licences
    s.add("Clinique", "SANTAREX", "annuel", 500000, today - timedelta(days=350))      # fin dans ~15 jours
    s.add("Essai", "LOKATIVO", "essai", 0, today - timedelta(days=20), today + timedelta(days=5))
    s.add("Gratuit", "SCOLABY", "gratuit", 0, today - timedelta(days=100))
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    text = c.get("/journee").text
    assert "1 licence(s) payante(s) arrivent à échéance" in text         # l'essai et le gratuit ne comptent pas
    assert "/licences?filtre=bientot" in text


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
