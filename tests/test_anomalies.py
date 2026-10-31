"""Surveillance des sites, bugs signalés et page Anomalies (lecture seule)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ibig_agent.anomalies import AnomalyService, Target
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import ProcessedMessage, Ticket, utcnow

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
HOME = ('<a href="/contact">Contact</a> <a href="/cassee">Cassée</a> <a href="mailto:x@y.ci">m</a>'
        ' <a href="https://autre.com/x">ext</a> Écrire à inconnu@site-test.ci ou +225 07 11 22 33 44')


def service(rt, state, alerts=None, cert=None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if state.get("down"):
            return httpx.Response(503, text="panne")
        if path == "/cassee":
            return httpx.Response(404)
        return httpx.Response(200, text=HOME)

    svc = AnomalyService(rt.sessions, rt.settings, rt.org, rt.kb,
                         alert=(lambda *a: alerts.append(a)) if alerts is not None else None,
                         client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cert_check=cert or (lambda host: None), pause=0)
    svc.targets = lambda: [Target("Site test", "https://site-test.ci", "site", "SOFT")]
    return svc


def test_a_site_down_is_critical_alerts_once_and_resolves_when_back(rt):
    alerts, state = [], {"down": True}
    svc = service(rt, state, alerts)
    svc.scan_sites(NOW)
    (f,) = [x for x in svc.findings() if x.check == "dispo"]
    assert f.severity == "critical" and "503" in f.title and f.status == "open"
    svc.scan_sites(NOW + timedelta(hours=1))
    assert len(alerts) == 1 and svc.findings()[0].count == 2          # une seule alerte, pas un spam
    state["down"] = False
    svc.scan_sites(NOW + timedelta(hours=2))
    assert svc.findings()[0].status == "resolved" and svc.open_count() == 0
    state["down"] = True
    svc.scan_sites(NOW + timedelta(hours=3))
    assert svc.findings()[0].status == "open" and len(alerts) == 2    # rouvert : nouvelle alerte


def test_deep_check_finds_broken_links_and_unknown_contacts(rt):
    svc = service(rt, {})
    svc.scan_sites(NOW)                                               # léger : ni liens ni contacts
    assert svc.findings() == []
    svc.scan_sites(NOW, deep=True)
    by = {f.check: f for f in svc.findings()}
    assert "liens" in by and "/cassee" in by["liens"].detail and "autre.com" not in by["liens"].detail
    kinds = [f for f in svc.findings() if f.check == "contacts"]
    assert any("inconnu@site-test.ci" in f.detail for f in kinds)
    assert any("225" in f.detail for f in kinds) and all(f.severity == "info" for f in kinds)


def test_expiring_certificate_is_flagged(rt):
    svc = service(rt, {}, cert=lambda host: 2)
    svc.scan_sites(NOW)
    (f,) = svc.findings()
    assert f.check == "ssl" and f.severity == "critical" and "2 jour" in f.title


def test_client_bug_reports_are_grouped_by_product_and_resolve(rt, monkeypatch):
    svc = service(rt, {})
    monkeypatch.setattr(svc, "_products", lambda: {"stockflow erp": "SOFT"})
    with rt.sessions() as s:
        for n in range(3):
            s.add(Ticket(channel="mail", pole="SOFT", created_at=NOW - timedelta(days=n),
                         question=f"STOCKFLOW ERP ne fonctionne plus depuis ce matin ({n})"))
        s.add(ProcessedMessage(mailbox="a@b.ci", message_id="<1>", subject="Erreur sur Scolaby",
                               pole="SOFT", category="support", processed_at=NOW))
        s.add(ProcessedMessage(mailbox="a@b.ci", message_id="<2>", subject="Merci pour la démo",
                               pole="SOFT", category="support", processed_at=NOW))
        s.commit()
    out = svc.scan_bugs(NOW)
    assert out["signals"] == 4 and out["products"] == 2
    bugs = {f.target: f for f in svc.findings() if f.check == "bug"}
    assert bugs["STOCKFLOW ERP"].severity == "warning" and "ticket n°" in bugs["STOCKFLOW ERP"].detail
    assert bugs["Pôle SOFT"].severity == "info"
    svc.scan_bugs(NOW + timedelta(days=60))                           # plus aucun signalement récent
    assert all(f.status == "resolved" for f in svc.findings())


def test_status_changes_and_extra_urls(rt):
    svc = service(rt, {"down": True})
    svc.scan_sites(NOW)
    key = svc.findings()[0].key
    svc.set_status(key, "ack", "Awa")
    assert svc.findings()[0].status == "ack" and svc.open_count() == 0
    with pytest.raises(ValueError):
        svc.set_status("inconnue", "ack", "Awa")
    with pytest.raises(ValueError):
        svc.set_status(key, "supprime", "Awa")
    assert svc.set_extra_urls("https://app.exemple.ci\nn'importe quoi\nhttp://b.ci", "Awa") == 2


def test_anomalies_page_and_access(rt, monkeypatch):
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("v@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    svc = service(rt, {"down": True})
    svc.scan_sites(utcnow())
    dg = client("dg@ibig.test")
    text = dg.get("/anomalies").text
    assert "répond en erreur 503" in text and "Urgent" in text
    assert 'href="/anomalies"' in dg.get("/").text                    # entrée du menu, avec pastille
    key = svc.findings()[0].key
    dg.post("/anomalies/statut", data={"cle": key, "statut": "resolved"})
    assert "répond en erreur 503" not in dg.get("/anomalies").text
    assert "répond en erreur 503" in dg.get("/anomalies?tous=1").text
    assert dg.post("/anomalies/adresses", data={"adresses": "https://a.exemple.ci"},
                   follow_redirects=False).status_code == 303
    v = client("v@ibig.test")
    assert v.get("/anomalies").status_code == 403
    assert v.post("/anomalies/verifier").status_code == 403
