"""Audience : réception des visites, protection de la vie privée, page d'analyse (aucun accès réseau)."""

import json

from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import EcoAsset, Visit

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Mobile Safari"


def add_site(rt, url="https://ibigsoft.com"):
    with rt.sessions() as s:
        s.add(EcoAsset(name="IBIG SOFT", url=url, kind="site"))
        s.commit()


def test_known_site_is_recorded_anonymously_and_unknown_or_bots_are_ignored(rt):
    add_site(rt)
    a = rt.analytics
    assert a.record("www.ibigsoft.com", "/tarifs.php?x=1", "pageview", "", "https://www.google.com/", UA, "1.2.3.4")
    assert not a.record("pirate.example", "/", "pageview", "", "", UA, "1.2.3.4")       # site inconnu
    assert not a.record("ibigsoft.com", "/", "pageview", "", "", "Googlebot/2.1", "1.2.3.4")   # robot
    assert not a.record("ibigsoft.com", "/", "pageview", "", "", "", "1.2.3.4")                # sans navigateur
    with rt.sessions() as s:
        rows = s.query(Visit).all()
    assert len(rows) == 1
    v = rows[0]
    assert v.site == "ibigsoft.com" and v.path == "/tarifs.php" and v.ref == "google.com"
    assert v.device == "mobile" and len(v.visitor) == 16 and "1.2.3.4" not in v.visitor


def test_internal_referrer_is_dropped_and_abuse_is_limited(rt):
    add_site(rt)
    a = rt.analytics
    a.record("ibigsoft.com", "/", "pageview", "", "https://ibigsoft.com/autre", UA, "9.9.9.9")
    with rt.sessions() as s:
        assert s.query(Visit).one().ref == ""
    results = [a.record("ibigsoft.com", "/", "pageview", "", "", UA, "7.7.7.7") for _ in range(120)]
    assert results.count(True) <= 90 and results[-1] is False


def test_overview_and_detail_count_views_visitors_and_actions(rt):
    add_site(rt)
    a = rt.analytics
    a.record("ibigsoft.com", "/", "pageview", "", "https://google.com", UA, "1.1.1.1")
    a.record("ibigsoft.com", "/contact.php", "pageview", "", "", UA, "1.1.1.1")
    a.record("ibigsoft.com", "/contact.php", "click", "whatsapp", "", UA, "1.1.1.1")
    a.record("ibigsoft.com", "/", "pageview", "", "", UA, "2.2.2.2")
    row = a.overview(7)[0]
    assert row == {"site": "ibigsoft.com", "visitors": 2, "views": 3, "actions": 1}
    d = a.detail("ibigsoft.com", 7)
    assert d["pages"][0] == ("/", 2) and ("click · whatsapp", 1) in d["actions"]
    assert d["series"][0]["views"] == 3


def test_public_endpoint_and_private_page(rt):
    add_site(rt)
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    js = c.get("/t.js")
    assert js.status_code == 200 and "/api/t" in js.text and "sendBeacon" in js.text
    body = json.dumps({"s": "ibigsoft.com", "p": "/", "e": "pageview", "l": "", "r": ""})
    r = c.post("/api/t", content=body, headers={"user-agent": UA, "origin": "https://ibigsoft.com"})
    assert r.status_code == 204
    assert c.post("/api/t", content="pas du json", headers={"user-agent": UA}).status_code == 204
    assert c.get("/audience", follow_redirects=False).status_code == 303        # exige une session
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    assert "ibigsoft.com" in c.get("/audience").text
