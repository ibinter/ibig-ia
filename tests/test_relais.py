"""Relais multi-comptes (Metricool, Buffer) : publication par un outil unique, avec la même
gouvernance que les connexions directes."""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from ibig_agent.channels.social import SocialError, publisher_for
from ibig_agent.db import ScheduledPost
from ibig_agent.governance import ActionRequest
from ibig_agent.publishing import (
    is_connected,
    load_credentials,
    publish_due,
    save_credentials,
    social_executors,
)

LATER = datetime(2100, 1, 1, tzinfo=UTC)


class FakeRelay:
    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        self.calls.append((request.method, request.url.host, request.url.path,
                           dict(request.url.params), dict(request.headers), body))
        if request.url.host == "app.metricool.com":
            if request.method == "POST":
                return httpx.Response(200, json={"data": {"id": 901}})
            return httpx.Response(200, json=[{"id": 77, "label": "IBIG"}])
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "updates": [{"id": "buf1"}]})
        return httpx.Response(200, json={"formatted_username": "@ibig"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def relay(rt):
    fake = FakeRelay()
    rt.governor.executors.update(social_executors(rt.sessions, rt.settings, fake.client))
    return fake


def validated(rt, reseau="facebook_page", compte="IBIG Soft"):
    pid = rt.governor.submit(ActionRequest(
        agent="communication", action_type="social.post", channel="facebook", account=compte,
        pole="SOFT", title=f"Lundi 05/10 · {reseau} · Nouvelle version",
        payload={"reseau": reseau, "compte": compte, "date": "2099-10-05",
                 "texte": "Scolaby 3 arrive.", "brief_visuel": ""})).pending_id
    return rt.governor.approve(pid, by="Awa <awa@ibig.test>")


def test_metricool_relay_publishes_validated_post(rt, relay):
    save_credentials(rt.sessions, rt.settings, "facebook_page", "IBIG Soft",
                     {"relais": "metricool", "relais_jeton": "mc-token",
                      "relais_profil": "77:12"}, "test")
    assert is_connected(rt.sessions, rt.settings, "facebook_page", "IBIG Soft")
    assert validated(rt).result["mode"] == "programme"
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 1
    method, host, path, params, headers, body = relay.calls[-1]
    assert (method, host, path) == ("POST", "app.metricool.com", "/api/v2/scheduler/posts")
    assert params == {"blogId": "77", "userId": "12"} and headers["x-mc-auth"] == "mc-token"
    sent = json.loads(body)
    assert sent["text"] == "Scolaby 3 arrive." and sent["providers"] == [{"network": "facebook"}]
    with rt.sessions() as s:
        sp = s.query(ScheduledPost).one()
    assert sp.status == "publie" and sp.result["relais"] == "metricool"


def test_buffer_relay_publishes_validated_post(rt, relay):
    save_credentials(rt.sessions, rt.settings, "x", "@InterMarkB",
                     {"relais": "buffer", "relais_jeton": "buf-token",
                      "relais_profil": "prof9"}, "test")
    validated(rt, "x", "@InterMarkB")
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 1
    _, host, path, _, _, body = relay.calls[-1]
    assert (host, path) == ("api.bufferapp.com", "/1/updates/create.json")
    form = parse_qs(body)
    assert form["profile_ids[]"] == ["prof9"] and form["text"] == ["Scolaby 3 arrive."]


def test_relay_replaces_direct_connection_and_must_be_complete(rt, relay):
    save_credentials(rt.sessions, rt.settings, "x", "@InterMarkB",
                     {"relais": "buffer", "relais_jeton": "buf-token"}, "test")
    assert not is_connected(rt.sessions, rt.settings, "x", "@InterMarkB")
    with pytest.raises(SocialError):
        publisher_for("x", load_credentials(rt.sessions, rt.settings, "x", "@InterMarkB"))
    assert validated(rt, "x", "@InterMarkB").result["mode"] == "manuel"


def test_unknown_relay_is_ignored(rt):
    save_credentials(rt.sessions, rt.settings, "x", "@InterMarkB",
                     {"relais": "zapier", "consumer_key": "a", "consumer_secret": "b",
                      "access_token": "c", "access_secret": "d"}, "test")
    assert load_credentials(rt.sessions, rt.settings, "x", "@InterMarkB")["relais"] == ""
    assert is_connected(rt.sessions, rt.settings, "x", "@InterMarkB")


def test_relay_check_reports_unknown_metricool_brand(rt, relay):
    creds = {"relais": "metricool", "relais_jeton": "t", "relais_profil": "99:12"}
    with pytest.raises(SocialError):
        publisher_for("facebook_page", creds, client=relay.client()).check()
    creds["relais_profil"] = "77:12"
    assert "accessible" in publisher_for("facebook_page", creds, client=relay.client()).check()
