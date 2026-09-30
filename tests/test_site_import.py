"""Import de la base de connaissances depuis les sites publics (section 7)."""

from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import KnowledgeEdit
from ibig_agent.site_import import Crawler

PAGES = {
    "https://ibigsoft.com/": """<html><head><title>IBIG SOFT</title><script>var x=1</script>
      </head><body><nav><a href="/solutions">Solutions</a> <a href="/contact">Contact</a>
      <a href="/login">Connexion</a> <a href="https://factpro.ibigsoft.com/">FactPro</a>
      <a href="https://www.facebook.com/ibigsoft/">Facebook</a></nav>
      <h1>Des logiciels qui font la différence</h1><p>Éditeur de logiciels SaaS.</p></body></html>""",
    "https://ibigsoft.com/solutions": "<h2>Nos solutions</h2><p>FactPro : facturation.</p>",
    "https://ibigsoft.com/contact": "<p>contact@ibigsoft.com · +225 07 00 00 00 00</p>",
    "https://ibig-digital.com/": "<h1>IBIG DIGITAL</h1><p>Digitalisation.</p>",
    "https://factpro.ibigsoft.com/": "<h1>IBIG FactPro</h1><p>Essai gratuit 7 jours. "
                                     "STARTER 4 900 FCFA / mois.</p>",
}


class FakeSites:
    def __init__(self):
        self.visited = []

    def handler(self, request):
        url = str(request.url)
        self.visited.append(url)
        if url in PAGES:
            return httpx.Response(200, text=PAGES[url],
                                  headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    def crawler(self):
        return Crawler(httpx.Client(transport=httpx.MockTransport(self.handler)),
                       check_host=lambda host: True)


EXTRACTED = {
    "promesse": "Des logiciels qui font la différence",
    "presentation": "Éditeur de logiciels SaaS.",
    "cibles": ["PME", "Commerçants"],
    "offres": [{"nom": "IBIG FactPro", "description": "Facturation", "pour_qui": "TPE/PME",
                "url": "https://factpro.ibigsoft.com", "essai": "7 jours",
                "prix": "STARTER 4 900 FCFA / mois", "duree": "À COMPLÉTER",
                "modalite": "À COMPLÉTER", "certification": "À COMPLÉTER",
                "sessions": "À COMPLÉTER", "source": "https://factpro.ibigsoft.com/",
                "arguments": ["QR anti-falsification", "Mobile Money"]}],
    "contacts": {"emails": ["contact@ibigsoft.com"], "telephones": ["+225 07 00 00 00 00"],
                 "whatsapp": [], "adresses": [], "horaires": [],
                 "reseaux_sociaux": ["https://www.facebook.com/ibigsoft/"],
                 "sites": ["https://ibigsoft.com"]},
    "faq": [{"question": "Y a-t-il un essai gratuit ?", "reponse": "Oui, 7 jours.",
             "source": "https://factpro.ibigsoft.com/"}],
    "ton_exemples": ["Des logiciels qui font la différence"],
}


@pytest.fixture
def sites(rt, llm):
    fake = FakeSites()
    rt.site_crawler = fake.crawler()
    original = llm.structured

    def structured(purpose, *a, **k):
        if purpose == "kb.site_import":
            llm.last_import_prompt = a[2]
            return EXTRACTED
        return original(purpose, *a, **k)

    llm.structured = structured
    return fake


def test_crawler_reads_site_and_linked_sites(sites):
    pages = sites.crawler().crawl("https://ibigsoft.com/")
    urls = [p.url for p in pages]
    assert "https://ibigsoft.com/solutions" in urls and "https://factpro.ibigsoft.com/" in urls
    assert not any("facebook" in u or "login" in u for u in sites.visited)
    home = pages[0]
    assert "var x" not in home.text and "## Des logiciels" in home.text


def test_import_fills_fiche_catalogue_faq_contacts(rt, sites, llm):
    out = rt.import_sites(["SOFT"])
    assert "offre" in out["SOFT"]
    assert "=== PAGE https://factpro.ibigsoft.com/" in llm.last_import_prompt
    fiche = rt.kb.raw("poles/soft.md")
    assert "Des logiciels qui font la différence" in fiche and "statut: a_completer" in fiche
    assert "## Interdits" in fiche  # sections non importées conservées
    assert "IBIG FactPro" in rt.kb.products("SOFT")
    assert "4 900 FCFA" in rt.kb.raw("catalogue-ibig-soft.md")
    assert any(f.question == "Y a-t-il un essai gratuit ?" for f in rt.kb.faq)
    assert "contact@ibigsoft.com" in rt.kb.raw("contacts.md")
    # Fiche jamais utilisable avant relecture humaine
    assert not rt.contenus_web.pole_ready("SOFT")


def test_import_never_overwrites_manual_edits(rt, sites):
    with rt.sessions() as s:
        s.add(KnowledgeEdit(path="poles/soft.md", content=rt.kb.raw("poles/soft.md")
                            .replace("À COMPLÉTER", "Rédigé par la direction", 1),
                            updated_by="Direction <dg@ibig.test>"))
        s.commit()
    out = rt.import_sites(["SOFT"])
    assert "non écrasés" in out["SOFT"] and "fiche" in out["SOFT"]
    assert "Rédigé par la direction" in rt.kb.raw("poles/soft.md")
    # Deux pôles : les contacts de chacun sont conservés
    rt.import_sites(["DIGITAL"])
    contacts = rt.kb.raw("contacts.md")
    assert "Contacts — IBIG SOFT" in contacts and "Contacts — IBIG DIGITAL" in contacts


def test_dashboard_import_card(rt, sites):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction", "motdepasse-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": "motdepasse-solide"})
    page = c.get("/connaissances").text
    assert "Importer depuis vos sites" in page and "ibigsoft.com" in page
    r = c.post("/connaissances/importer", data={"pole": "SOFT"}, follow_redirects=False)
    assert "Lecture des sites lancée" in unquote(r.headers["location"])


def test_internal_addresses_are_refused():
    from ibig_agent.site_import import _public_host

    assert not _public_host("localhost") and not _public_host("127.0.0.1")
    assert Crawler(check_host=_public_host).fetch("http://127.0.0.1:8000/")[0] is None
