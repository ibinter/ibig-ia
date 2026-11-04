"""Audit de référencement : règles, note, exploration, mission (aucun accès réseau réel)."""

import httpx

from ibig_agent.db import EcoAsset
from ibig_agent.seo import Issue, SeoService, analyze_page, grade, score

GOOD = ("<html lang='fr'><head><title>IBIG SOFT, logiciels de gestion pour entreprises à Abidjan</title>"
        "<meta name='description' content='IBIG SOFT conçoit des logiciels de gestion simples et fiables pour les "
        "entreprises et les écoles de Côte d Ivoire.'><meta name='viewport' content='width=device-width'>"
        "<link rel='canonical' href='https://x.ci/'><meta property='og:title' content='a'>"
        "<meta property='og:image' content='b'><script type='application/ld+json'>{}</script></head>"
        "<body><h1>Logiciels</h1><img src='a.png' alt='logo'><p>" + "mot " * 300 + "</p>"
        "<a href='/produits'>Produits</a></body></html>")


def codes(issues):
    return {i.code for i in issues}


def test_good_page_has_no_issue_and_bad_page_is_flagged():
    assert analyze_page(GOOD, "https://x.ci/")[0] == []
    bad, _ = analyze_page("<html><head><meta name='robots' content='noindex'></head>"
                          "<body><img src='a'></body></html>", "https://x.ci/")
    assert {"noindex", "title_absent", "desc_absente", "h1_absent", "viewport", "images_alt",
            "contenu_court"} <= codes(bad)


def test_score_is_capped_per_code_and_graded():
    many = [Issue("title_absent", "critical", f"/{n}", "", "") for n in range(5)]
    assert score(many) == 60          # 5 x 25 plafonné à 40
    assert score([]) == 100 and grade(100) == "excellent" and grade(40) == "faible"


def make_site(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def one_asset(rt, name, url):
    with rt.sessions() as s:
        a = EcoAsset(name=name, url=url, kind="site")
        s.add(a)
        s.commit()
        s.refresh(a)
    return a


def test_audit_detects_blocking_robots_missing_sitemap_and_broken_page(rt):
    a = one_asset(rt, "Site test", "https://x.ci")
    service = SeoService(rt.sessions, rt.settings, lambda: [a], client=make_site({
        "/": GOOD, "/robots.txt": "User-agent: *\nDisallow: /\n", "/produits": "<html><body>vide</body></html>"}))
    r = service.audit(a)
    found = codes([Issue(**i) for i in r["issues"]])
    assert {"robots_bloque", "sitemap", "title_absent"} <= found
    assert r["score"] < 100 and service.result(a.id)["score"] == r["score"]
    again = service.audit(a)
    assert again["previous"] == r["score"] and len(again["history"]) == 2
    title, _goal, steps = service.fix_plan(a.id)
    assert "référencement" in title and steps


def test_unreachable_home_is_critical(rt):
    a = one_asset(rt, "Mort", "https://mort.ci")
    r = SeoService(rt.sessions, rt.settings, lambda: [a], client=make_site({})).audit(a)
    assert r["issues"][0]["code"] == "inaccessible" and r["score"] == 75


def test_pages_sites_seo_and_mission(rt):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app
    a = one_asset(rt, "Site pages", "https://pages.ci")
    SeoService(rt.sessions, rt.settings, lambda: [a],
               client=make_site({"/": GOOD})).audit(a)
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    assert "Site pages" in c.get("/sites").text
    assert "Site pages" in c.get("/seo").text
    assert "Correction" in c.get(f"/seo/{a.id}").text
    r = c.post(f"/seo/{a.id}/mission", follow_redirects=False)
    assert r.status_code == 303 and "/missions/" in r.headers["location"]
