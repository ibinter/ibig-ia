"""Campagnes par Mailaby (module d'emailing de l'ERP) : mêmes règles que Brevo — rédigées
par l'agent, validées par un humain — mais envoyées par le point d'entrée de Mailaby."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.channels.emailing import EmailingError
from ibig_agent.channels.mailaby import MailabyClient
from ibig_agent.configstore import service_value
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import PendingAction, ServiceSetting
from ibig_agent.runtime import emailing_executors

PASSWORD = "mot-de-passe-solide"
URL = "https://erp.ibig.test/admin/mailaby"
ACCOUNTS = [
    {"id": 4, "from_name": "IBIG SARL", "from_email": "contact@intermark-business.com"},
    {"id": 2, "from_name": "IBIG Soft", "from_email": "contact@ibigsoft.com"},
    {"id": 5, "from_name": "RECRUTEMENT IBIG EDUFORM", "from_email": "recrutement@ibig-eduform.com"},
    {"id": 7, "from_name": "FORMATION IBIG EDUFORM", "from_email": "formation@ibig-eduform.com"},
]


class FakeMailaby:
    """Point d'entrée api_agent.php simulé : enregistre les appels."""

    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.params.get("action"), body,
                           request.headers.get("x-agent-token")))
        if request.url.path != "/admin/mailaby/api_agent.php":
            return httpx.Response(404, text="introuvable")
        if request.headers.get("x-agent-token") != "jeton-agent":
            return httpx.Response(403, json={"error": "Jeton invalide"})
        action = request.url.params.get("action")
        if action == "ping":
            return httpx.Response(200, json={"ok": True})
        if action == "accounts":
            return httpx.Response(200, json={"accounts": ACCOUNTS})
        if action == "tags":
            return httpx.Response(200, json={"total_actifs": 900, "tags": [
                {"id": 5, "name": "Clients SOFT", "n": 420}]})
        if action == "stats":
            return httpx.Response(200, json={"days": 7, "desinscrits": 12, "campagnes": [
                {"id": 31, "name": "Formations", "status": "sent", "sent": 200,
                 "opened": 80, "clicked": 10, "bounced": 20},
                {"id": 32, "name": "Petite", "status": "sent", "sent": 10,
                 "opened": 5, "clicked": 1, "bounced": 5}]})
        if action == "campaign":
            return httpx.Response(201, json={"id": 31, "status": "sending", "recipients": 420})
        return httpx.Response(404, json={"error": "Action inconnue"})

    def client(self, url, token):
        return MailabyClient(url, token, httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def mailaby(rt):
    fake = FakeMailaby()
    rt.mailaby_factory = fake.client
    rt.governor.executors.update(
        emailing_executors(rt.sessions, rt.settings, mailaby_factory=fake.client))
    return fake


@pytest.fixture
def dg(rt):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", PASSWORD)
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": PASSWORD})
    return c


def test_client_checks_token_and_lists_tags(mailaby):
    with pytest.raises(EmailingError, match="403.*Jeton invalide"):
        mailaby.client(URL, "mauvais").check()
    with pytest.raises(EmailingError, match="requis"):
        MailabyClient("", "")
    ok = mailaby.client(URL, "jeton-agent")
    assert "4 compte" in ok.check()
    lists = ok.lists()
    assert (lists[0].id, lists[0].subscribers) == (0, 900)   # « tous les contacts actifs »
    assert (lists[1].id, lists[1].name, lists[1].subscribers) == (5, "Clients SOFT", 420)


def test_send_campaign_targets_tags_and_schedule(mailaby):
    client = mailaby.client(URL, "jeton-agent")
    out = client.send_campaign("N1", "Objet", "<p>Bonjour</p>", "IBIG SARL", "n@ibig.test",
                               [5], datetime(2026, 11, 2, 9, 30, tzinfo=UTC))
    _, _, body, _ = mailaby.calls[-1]
    assert body["tag_ids"] == [5] and body["send_at"] == "2026-11-02 09:30:00"
    assert body["launch"] is True and body["from_email"] == "n@ibig.test"
    assert out["campaign_id"] == 31 and out["destinataires"] == 420
    client.send_campaign("N2", "Objet", "<p>x</p>", "IBIG", "n@ibig.test", [0])
    assert mailaby.calls[-1][2]["tag_ids"] == [] and "send_at" not in mailaby.calls[-1][2]


def test_campaign_goes_through_mailaby_only_after_validation(rt, dg, mailaby, connector):
    dg.post("/services/mailaby", data={"adresse": URL, "jeton": "jeton-agent",
                                       "expediteur": "IBIG SARL",
                                       "expediteur_mail": "n@ibig.test"})
    with rt.sessions() as s:
        assert "jeton-agent" not in s.get(ServiceSetting, "mailaby_token").value  # chiffré
    assert service_value(rt.sessions, rt.settings, "mailaby_token") == "jeton-agent"
    assert "Clients SOFT (420 contacts)" in dg.get("/campagnes").text
    dg.post("/campagnes", data={"pole": "SOFT", "sujet": "Formations", "liste": "5|Clients SOFT"})
    with rt.sessions() as s:
        pa = s.query(PendingAction).filter_by(action_type="campaign.mail").one()
    assert pa.status == "pending"
    assert not any(action == "campaign" for _, action, _, _ in mailaby.calls)  # rien avant l'accord
    dg.post(f"/validations/{pa.id}/approuver")
    body = next(b for _, a, b, _ in mailaby.calls if a == "campaign")
    assert body["tag_ids"] == [5] and "{{ unsubscribe }}" not in body["html"]
    assert connector.sent == []  # jamais par une boîte LWS ou Gmail


def test_mailaby_settings_are_validated_and_removable(rt, dg, mailaby):
    refused = dg.post("/services/mailaby", data={"adresse": "http://erp.ibig.test",
                                                 "jeton": "x"}, follow_redirects=False)
    assert "https" in refused.headers["location"]
    assert service_value(rt.sessions, rt.settings, "mailaby_token") == ""
    dg.post("/services/mailaby", data={"adresse": URL, "jeton": "jeton-agent"})
    tested = dg.post("/services/mailaby/tester", follow_redirects=False)
    assert "Mailaby+OK" in tested.headers["location"] or "Mailaby%20OK" in tested.headers["location"]
    dg.post("/services/mailaby/retirer")
    assert service_value(rt.sessions, rt.settings, "mailaby_token") == ""


def test_one_connection_gives_all_accounts_and_picks_the_sender_by_pole(mailaby):
    from ibig_agent.channels.mailaby import default_sender

    accounts = mailaby.client(URL, "jeton-agent").accounts()
    assert len(accounts) == 4
    assert default_sender("SOFT", accounts) == "contact@ibigsoft.com"
    # plusieurs comptes EDUFORM : jamais « recrutement » pour une newsletter
    assert default_sender("EDUFORM", accounts) == "formation@ibig-eduform.com"
    assert default_sender("MULTISERVICES", accounts) == "contact@intermark-business.com"
    assert default_sender("SOFT", []) == ""


def test_campaign_uses_the_account_of_the_pole_or_the_one_chosen(rt, dg, mailaby, connector):
    dg.post("/services/mailaby", data={"adresse": URL, "jeton": "jeton-agent"})  # sans expéditeur
    page = dg.get("/campagnes").text
    assert "4 comptes d" in page and "contact@ibigsoft.com" in page
    dg.post("/campagnes", data={"pole": "SOFT", "sujet": "Promo", "liste": "5|Clients SOFT"})
    dg.post("/campagnes", data={"pole": "SOFT", "sujet": "Autre", "liste": "5|Clients SOFT",
                                "compte": "formation@ibig-eduform.com"})
    refused = dg.post("/campagnes", data={"pole": "SOFT", "sujet": "X", "liste": "5|C",
                                          "compte": "inconnu@ailleurs.test"},
                      follow_redirects=False)
    assert "inconnu" in refused.headers["location"].lower() or "Refus" in refused.headers["location"]
    with rt.sessions() as s:
        rows = s.query(PendingAction).filter_by(action_type="campaign.mail").order_by(
            PendingAction.id).all()
    assert [r.payload["expediteur_mail"] for r in rows] == [
        "contact@ibigsoft.com", "formation@ibig-eduform.com"]
    assert rows[0].payload["expediteur"] == "IBIG Soft"


def test_mailaby_stats_feed_indicators_and_bounce_alert(rt, dg, mailaby):
    from ibig_agent.db import JournalEntry

    assert not [i for i in rt.veille.indicators(7).items if "Campagnes mail" in i.nom]
    dg.post("/services/mailaby", data={"adresse": URL, "jeton": "jeton-agent"})
    items = {i.nom: i for i in rt.veille.indicators(7).items if "Campagnes mail" in i.nom}
    assert items["Campagnes mail : taux d'ouverture"].valeur == "40 %"   # 85 / 210
    rejet = items["Campagnes mail : taux de rejet"]
    assert rejet.valeur == "11.9 %" and rejet.etat == "alerte"   # 25 rejets / 210 envois
    assert rt.veille.check_alerts().rejets == 1   # « Petite » (10 envois) est ignorée
    assert rt.veille.check_alerts().rejets == 0   # une alerte par campagne
    with rt.sessions() as s:
        alert = s.query(JournalEntry).filter_by(action_type="veille.alert").one()
    assert alert.details["kind"] == "rejets" and alert.details["refs"] == ["31"]
