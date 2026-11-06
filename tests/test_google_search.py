"""Positions sur Google : connexion signée, lecture de Search Console, page de résultats (aucun accès réseau réel)."""

import json
from datetime import date

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.configstore import set_service_value
from ibig_agent.dashboard.app import create_app
from ibig_agent.google_search import KEY_NAME, GoogleSearchService


def fake_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "client_email": "agent@test.iam.gserviceaccount.com",
                       "private_key": pem})


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "oauth2.googleapis.com":
        assert b"jwt-bearer" in request.content and b"assertion=" in request.content
        return httpx.Response(200, json={"access_token": "jeton"})
    assert request.headers["authorization"] == "Bearer jeton"
    if "intermark" in str(request.url):
        return httpx.Response(403)                              # propriété où le compte n'est pas ajouté
    dims = json.loads(request.content).get("dimensions")
    if dims is None:
        return httpx.Response(200, json={"rows": [{"clicks": 12, "impressions": 300, "ctr": 0.04, "position": 8.3}]})
    if dims == ["date"]:
        return httpx.Response(200, json={"rows": [{"keys": ["2026-11-01"], "clicks": 5, "impressions": 100}]})
    if dims == ["query"]:
        return httpx.Response(200, json={"rows": [{"keys": ["logiciel de gestion abidjan"], "clicks": 3,
                                                   "impressions": 40, "position": 4.2}]})
    return httpx.Response(200, json={"rows": [{"keys": ["https://ibigsoft.com/tarifs.php"], "clicks": 2,
                                               "impressions": 30, "position": 6.1}]})


def service(rt, domains=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GoogleSearchService(rt.sessions, rt.settings, client=client,
                               domains=domains or ["ibigsoft.com", "intermark-business.com"],
                               today=lambda: date(2026, 11, 6))


def test_sync_signs_a_token_reads_each_site_and_isolates_a_refused_one(rt):
    set_service_value(rt.sessions, rt.settings, KEY_NAME, fake_key(), "test", secret=True)
    s = service(rt)
    assert s.configured()
    out = s.sync()
    assert out["sites"] == 1 and "intermark-business.com" in out["erreurs"]
    rows = {r["domain"]: r for r in s.overview()}
    d = rows["ibigsoft.com"]["data"]
    assert d["clicks"] == 12 and d["impressions"] == 300 and d["ctr"] == 4.0 and d["position"] == 8.3
    assert d["to"] == "2026-11-04" and d["from"] == "2026-10-08"            # 28 jours, 2 jours de retard
    assert d["queries"][0]["q"] == "logiciel de gestion abidjan" and d["pages"][0]["clicks"] == 2
    assert rows["intermark-business.com"]["data"] is None and "Accès refusé" in rows["intermark-business.com"]["error"]
    assert s.detail("ibigsoft.com")["daily"][0]["day"] == "2026-11-01" and s.detail("inconnu.com") is None


def test_without_a_key_nothing_is_configured_and_sync_explains(rt):
    s = service(rt)
    assert not s.configured()
    with pytest.raises(ValueError, match="pas configurée"):
        s.sync()


def test_page_is_private_and_shows_the_figures(rt):
    set_service_value(rt.sessions, rt.settings, KEY_NAME, fake_key(), "test", secret=True)
    service(rt, ["ibigsoft.com"]).sync()
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    assert c.get("/google", follow_redirects=False).status_code == 303
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    text = c.get("/google").text
    assert "ibigsoft.com" in text and "Positions sur Google" in text
