"""Publication centrale d'articles : validation obligatoire, noms contrôlés, publication tracée et retirable."""

import pytest
from fastapi.testclient import TestClient

from ibig_agent.articles import ArticleService, Published, slugify
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app

LONG = "## Pourquoi digitaliser\n" + ("Une école bien gérée gagne du temps chaque jour. " * 20)


class FakeSite:
    key, label = "TEST", "Site de test"

    def __init__(self, ready=True, fail=False):
        self._ready, self._fail, self.removed, self.published = ready, fail, [], []

    def ready(self):
        return self._ready

    def publish(self, article):
        if self._fail:
            raise RuntimeError("base injoignable")
        self.published.append(article.slug)
        return Published("ext-1", f"https://exemple.test/blog/{article.slug}")

    def unpublish(self, external_id):
        self.removed.append(external_id)


class FakeLlm:
    def __init__(self, answer):
        self.answer = answer

    def write(self, purpose, system, user, max_tokens=4000):
        assert "IBI GROUP SARL" in system and "jamais" in system.lower()
        return self.answer


def service(rt, site=None, llm=None):
    return ArticleService(rt.sessions, rt.settings, llm=llm, connectors=[site or FakeSite()])


def test_slug_is_clean():
    assert slugify("Pourquoi digitaliser l'école ?") == "pourquoi-digitaliser-l-ecole"


def test_forbidden_name_is_refused_everywhere(rt):
    s = service(rt)
    with pytest.raises(ValueError):
        s.create("IBIG Group lance un site", LONG, "dg")
    aid = s.create("Un bon titre", LONG, "dg")
    with pytest.raises(ValueError):
        s.update(aid, "Titre", "Le groupe ibig  group est ici", "", "", "", "", "")
    s.create("Avec les trois noms", "IBIG SARL, INTERMARK BUSINESS INTERNATIONAL GROUP, IBI GROUP SARL", "dg")


def test_publication_requires_validation_and_a_change_revokes_it(rt):
    site = FakeSite()
    s = service(rt, site)
    aid = s.create("Digitaliser son école", LONG, "dg")
    with pytest.raises(ValueError):
        s.publish(aid, ["TEST"], "dg")                              # brouillon : refusé
    short = s.create("Trop court", "quelques mots", "dg")
    with pytest.raises(ValueError):
        s.approve(short)
    s.approve(aid)
    s.update(aid, "Digitaliser son école", LONG + " Encore.", "", "", "", "", "")   # modification : repasse en brouillon
    with pytest.raises(ValueError):
        s.publish(aid, ["TEST"], "dg")
    s.approve(aid)
    res = s.publish(aid, ["TEST"], "dg")
    assert res == [{"target": "TEST", "status": "ok", "url": "https://exemple.test/blog/digitaliser-son-ecole"}]
    assert s.publish(aid, ["TEST"], "dg")[0]["status"] == "deja" and len(site.published) == 1     # jamais deux fois
    art, pubs = s.get(aid)
    assert art.status == "publie" and pubs[0].status == "ok"
    s.unpublish(aid, "TEST", "dg")
    assert site.removed == ["ext-1"] and s.get(aid)[1][0].status == "retire"


def test_a_failing_site_is_recorded_and_unknown_or_unready_sites_are_refused(rt):
    s = service(rt, FakeSite(fail=True))
    aid = s.create("Titre", LONG, "dg")
    s.approve(aid)
    res = s.publish(aid, ["TEST"], "dg")
    assert res[0]["status"] == "erreur" and "base injoignable" in res[0]["error"]
    assert s.get(aid)[0].status == "pret" and s.get(aid)[1][0].status == "erreur"
    with pytest.raises(ValueError):
        s.publish(aid, ["INCONNU"], "dg")
    with pytest.raises(ValueError):
        service(rt, FakeSite(ready=False)).publish(aid, ["TEST"], "dg")


def test_ai_draft_is_parsed_and_checked(rt):
    answer = ('Voici : {"title": "Digitaliser son école en 2026", "excerpt": "Gagnez du temps.", '
              '"content": "## Intro\\nTexte.", "seo_title": "Digitaliser son école", '
              '"seo_description": "Guide pratique.", "category": "Éducation"}')
    s = service(rt, llm=FakeLlm(answer))
    art = s.get(s.draft_with_ai("digitaliser l'école", "gain de temps", "dg"))[0]
    assert art.title.startswith("Digitaliser") and art.category == "Éducation" and art.status == "brouillon"
    with pytest.raises(ValueError):
        service(rt, llm=FakeLlm("pas de json")).draft_with_ai("sujet", "", "dg")
    with pytest.raises(ValueError):
        service(rt, llm=FakeLlm('{"title": "IBIG Group", "content": "x"}')).draft_with_ai("sujet", "", "dg")


def test_page_is_for_direction_only(rt):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    assert c.get("/articles", follow_redirects=False).status_code == 303
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    r = c.post("/articles", data={"titre": "Mon article", "contenu": LONG}, follow_redirects=False)
    assert r.status_code == 303 and "/articles?id=" in r.headers["location"]
    page = c.get(r.headers["location"]).text
    assert "Mon article" in page and "Valider" in page
    bad = c.post("/articles", data={"titre": "IBIG Group", "contenu": "x"}, follow_redirects=False)
    assert "msg=" in bad.headers["location"]


def test_to_html_escapes_everything_and_keeps_the_simple_format():
    from ibig_agent.articles import to_html

    out = to_html("## Titre <b>\n\nUn **gras** et <script>x</script>.\n- a\n- b\n### Sous\nFin")
    assert "<h2>Titre &lt;b&gt;</h2>" in out and "<strong>gras</strong>" in out
    assert "<script>" not in out and "<ul><li>a</li><li>b</li></ul>" in out and "<h3>Sous</h3>" in out


def test_intermark_connector_signs_requests_and_reports_refusals(rt):
    import hashlib
    import hmac
    import json

    import httpx

    from ibig_agent.articles import IntermarkConnector

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        ts, sig = request.headers["x-ibig-timestamp"], request.headers["x-ibig-signature"]
        expected = hmac.new(b"s" * 40, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return httpx.Response(401, json={"ok": False, "error": "Signature invalide"})
        data = json.loads(body)
        seen[data["action"]] = data
        if data["action"] == "publish":
            return httpx.Response(200, json={"ok": True, "id": 12, "url": "https://intermark-business.com/actualites/x"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    good = IntermarkConnector(lambda: "s" * 40, client=client)
    assert good.ready() and good.ping()
    s = service(rt)
    aid = s.create("Un titre", "## A\nTexte <b>risqué</b>.", "dg")
    art = s.get(aid)[0]
    pub = good.publish(art)
    assert pub.external_id == "12" and pub.url.endswith("/actualites/x")
    assert "<h2>A</h2>" in seen["publish"]["contenu"] and "<b>" not in seen["publish"]["contenu"]
    good.unpublish("12")
    assert seen["unpublish"]["id"] == 12
    bad = IntermarkConnector(lambda: "x" * 40, client=client)
    with pytest.raises(ValueError, match="Signature invalide"):
        bad.ping()
    assert not IntermarkConnector(lambda: "", client=client).ready()
