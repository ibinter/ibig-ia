"""Campagnes mail par Brevo (sections 9 et 12) : rédigées par l'agent, validées par un
humain, envoyées par le service d'emailing, jamais par les boîtes LWS ou Gmail."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.channels.emailing import BrevoClient, EmailingError
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import PendingAction, ServiceSetting
from ibig_agent.runtime import emailing_executors

PASSWORD = "mot-de-passe-solide"


class FakeBrevo:
    """API Brevo simulée : enregistre les appels."""

    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path,
                           json.loads(request.content) if request.content else None,
                           request.headers.get("api-key")))
        if request.headers.get("api-key") != "cle-brevo":
            return httpx.Response(401, json={"message": "Key not found"})
        path = request.url.path
        if path == "/v3/account":
            return httpx.Response(200, json={"companyName": "IBIG SARL"})
        if path == "/v3/contacts/lists":
            return httpx.Response(200, json={"lists": [
                {"id": 7, "name": "Clients SOFT", "uniqueSubscribers": 420}]})
        if path == "/v3/emailCampaigns":
            return httpx.Response(201, json={"id": 99})
        if path == "/v3/emailCampaigns/99/sendNow":
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "?"})

    def client(self, key):
        return BrevoClient(key, httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def brevo(rt):
    fake = FakeBrevo()
    rt.brevo_factory = fake.client
    rt.governor.executors.update(emailing_executors(rt.sessions, rt.settings, fake.client))
    return fake


@pytest.fixture
def dg(rt):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", PASSWORD)
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": PASSWORD})
    return c


def test_client_reports_brevo_errors(brevo):
    with pytest.raises(EmailingError, match="401"):
        brevo.client("mauvaise-cle").check()
    assert brevo.client("cle-brevo").lists()[0].subscribers == 420
    with pytest.raises(EmailingError, match="absente"):
        BrevoClient("")


def test_campaign_is_sent_by_brevo_only_after_validation(rt, dg, brevo, connector):
    dg.post("/services/brevo", data={"cle": "cle-brevo", "expediteur": "IBIG SARL",
                                     "expediteur_mail": "newsletter@ibig.test"})
    with rt.sessions() as s:
        assert "cle-brevo" not in s.get(ServiceSetting, "brevo_api_key").value  # chiffrée
    assert "Clients SOFT (420 contacts)" in dg.get("/campagnes").text
    dg.post("/campagnes", data={"pole": "SOFT", "sujet": "Formations", "liste": "7|Clients SOFT"})
    with rt.sessions() as s:
        pa = s.query(PendingAction).filter_by(action_type="campaign.mail").one()
    assert pa.status == "pending" and pa.channel == "emailing"
    assert "<script>" not in pa.payload["contenu_html"]
    assert not any(path.startswith("/v3/emailCampaigns") for _, path, _, _ in brevo.calls)
    dg.post(f"/validations/{pa.id}/approuver")
    created = next(body for m, path, body, _ in brevo.calls if path == "/v3/emailCampaigns")
    assert created["recipients"] == {"listIds": [7]}
    assert created["sender"] == {"name": "IBIG SARL", "email": "newsletter@ibig.test"}
    assert "{{ unsubscribe }}" in created["htmlContent"]
    assert ("POST", "/v3/emailCampaigns/99/sendNow") in [(m, p) for m, p, _, _ in brevo.calls]
    assert connector.sent == []  # jamais par une boîte LWS ou Gmail


def test_stop_button_blocks_campaigns(rt, dg, brevo):
    dg.post("/services/brevo", data={"cle": "cle-brevo", "expediteur_mail": "n@ibig.test"})
    dg.post("/campagnes", data={"pole": "SOFT", "sujet": "Promo", "liste": "7|Clients SOFT"})
    dg.post("/arret", data={"canal": "emailing", "action": "stop", "motif": "test"})
    with rt.sessions() as s:
        pid = s.query(PendingAction).filter_by(action_type="campaign.mail").one().id
    r = dg.post(f"/validations/{pid}/approuver", follow_redirects=False)
    assert "Refus" in r.headers["location"]
    assert not any(p == "/v3/emailCampaigns" for _, p, _, _ in brevo.calls)
