"""Import du catalogue complet des produits IBIG (via l'API publique d'IBIG PARTNERS)."""

from pathlib import Path

import httpx
import pytest

from ibig_agent.catalog_import import (
    BRANCHES,
    CatalogError,
    import_catalog,
    merge,
    price_line,
)
from ibig_agent.knowledge import KnowledgeBase

EDU = [
    {"name": "DAF – Directeur Administratif & Financier", "slug": "daf", "price": 400000,
     "rate": 10, "siteUrl": "https://ibig-eduform.com/formation/daf", "description": "60 h",
     "category": "Direction & Administration",
     "grille": {"individuel_pres": 570000}},
    {"name": "DAF – Directeur Administratif & Financier", "slug": "daf-q4", "price": 400000,
     "rate": 10, "siteUrl": "https://ibig-eduform.com/formation/daf-q4", "description": "",
     "category": "Direction & Administration", "grille": {"individuel_pres": 570000}},
    {"name": "Intelligence Artificielle pour Professionnels", "slug": "ia", "price": 95000,
     "rate": 10, "siteUrl": "https://ibig-eduform.com/formation/ia", "description": "7 h",
     "category": "IA & Digitalisation"},
    {"name": "intelligence artificielle pour professionnels", "slug": "ia2", "price": 50000,
     "rate": 10, "siteUrl": "https://ibig-eduform.com/formation/ia2", "description": "",
     "category": "IA & Digitalisation"},
    {"name": "Odoo gratuit", "slug": "odoo", "price": 0, "rate": 10, "siteUrl": "",
     "description": "", "category": "Informatique & Tech"},
]
SOFT = [
    {"slug": "agri-m", "name": "AGRIFRIK — Basique", "pricingType": "MONTHLY_SUB",
     "price": 6500, "rate": 20, "siteUrl": "https://agrifrik.ibigsoft.com",
     "description": "ERP agricole. 6 500 FCFA/mois."},
    {"slug": "agri-a", "name": "AGRIFRIK — Basique (Annuel)", "pricingType": "ANNUAL_SUB",
     "price": 65000, "rate": 20, "siteUrl": "https://agrifrik.ibigsoft.com",
     "description": "Abonnement annuel."},
]


def fake_api():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/catalogue":
            return httpx.Response(200, json={"ok": True, "formations": EDU})
        if path == "/api/catalogue/ibig-soft":
            return httpx.Response(200, json={"branch": "ibig-soft", "products": SOFT})
        return httpx.Response(404, json={"error": "Branche introuvable"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_merge_keeps_every_price_and_flags_ambiguity():
    items = {i.name.lower(): i for i in merge(EDU)}
    assert len(items) == 3                              # 5 reçus, 2 doublons fusionnés
    daf = items["daf – directeur administratif & financier"]
    assert daf.prices == [400000] and len(daf.urls) == 2
    assert price_line(daf) == ("400 000 FCFA", False)
    ia = items["intelligence artificielle pour professionnels"]
    text, doubtful = price_line(ia)
    assert doubtful and "50 000 FCFA ; 95 000 FCFA" in text and "à confirmer" in text
    text, doubtful = price_line(items["odoo gratuit"])
    assert doubtful and "0 FCFA" not in text            # jamais « 0 FCFA » pour un prix absent


def test_import_writes_files_without_internal_data(tmp_path):
    result, stats = import_catalog(tmp_path, fake_api(), today="2026-10-30")
    assert result.counts == {"IBIG SOFT": 2, "IBIG EDUFORM": 3}
    assert "IBIG DIGITAL" in result.skipped              # marque sans produit : ignorée
    assert stats["ecrits"] == len(result.files) and stats["retires"] == 0
    everything = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.md"))
    assert "570" not in everything                      # grille calculée : jamais reprise
    assert "rate" not in everything.lower() and "commission" not in everything.lower()
    assert "- Prix : 6 500 FCFA par mois" in everything
    assert "- Type : formation" in everything           # EDUFORM : l'API ne le dit pas
    soft = (tmp_path / "produits" / "ibig-soft.md").read_text(encoding="utf-8")
    assert soft.startswith("---\ntitre: Catalogue IBIG SOFT\npole: SOFT\ntype: produits")


def test_import_is_idempotent_and_never_wipes_on_a_partial_outage(tmp_path):
    import_catalog(tmp_path, fake_api(), today="2026-10-30")
    _, stats = import_catalog(tmp_path, fake_api(), today="2026-10-30")
    assert stats["ecrits"] == 0 and stats["retires"] == 0
    stale = tmp_path / "produits" / "ancien.md"
    stale.write_text("x", encoding="utf-8")
    import_catalog(tmp_path, fake_api(), today="2026-10-30")
    assert stale.exists()          # marques absentes de l'API : on ne supprime rien


def test_import_refuses_to_write_when_nothing_is_received(tmp_path):
    empty = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={})))
    with pytest.raises(CatalogError, match="rien n'a été écrit"):
        import_catalog(tmp_path, empty)
    assert not any(tmp_path.rglob("*.md"))


def test_catalogue_is_searched_by_passage_and_never_injected_whole(tmp_path):
    (tmp_path / "charte.md").write_text(
        "---\ntitre: Charte\npole: GROUPE\ntype: charte\n---\nTon pédagogue.\n", encoding="utf-8")
    (tmp_path / "interdits.md").write_text(
        "---\ntitre: Interdits\npole: GROUPE\ntype: interdits\n---\nAucune promesse.\n",
        encoding="utf-8")
    import_catalog(tmp_path, fake_api(), today="2026-10-30")
    kb = KnowledgeBase(tmp_path)
    ctx = kb.context_for("SOFT", "abonnement AGRIFRIK mensuel")
    assert "Ton pédagogue" in ctx and "Aucune promesse" in ctx      # références toujours là
    assert "AGRIFRIK — Basique" in ctx and "6 500 FCFA par mois" in ctx
    assert len(ctx) < 12000
    found = kb.search_passages("formation DAF directeur financier", "EDUFORM")
    assert found and found[0].heading.startswith("DAF")
    assert "AGRIFRIK — Basique" in kb.products("SOFT", tout=True)
    assert kb.products("SOFT") == []                                 # le menu reste léger
    assert not kb.verify_facts("L'abonnement AGRIFRIK — Basique coûte 6 500 FCFA par mois.")
    assert kb.verify_facts("Promo à 1 234 FCFA seulement")           # prix inventé : repéré


def test_catalogue_price_only_validates_the_product_it_belongs_to(tmp_path):
    import_catalog(tmp_path, fake_api(), today="2026-10-30")
    kb = KnowledgeBase(tmp_path)
    # 65 000 FCFA existe au catalogue (abonnement annuel), pas pour un autre produit nommé
    assert not kb.verify_facts("AGRIFRIK — Basique (Annuel) : 65 000 FCFA par an.")
    issues = kb.verify_facts("AGRIFRIK — Basique : 65 000 FCFA par mois.")
    assert [i.kind for i in issues] == ["prix"]       # prix d'un autre produit : refusé
    assert [i.kind for i in kb.verify_facts("La formation DAF coûte 6 500 FCFA.")] == ["prix"]
    assert [i.kind for i in kb.verify_facts("Offre à 400 000 FCFA.")] == ["prix"]  # sans produit


def test_every_branch_maps_to_a_known_pole():
    poles = {"SOFT", "EDUFORM", "IMMOTRUST", "MARKET", "DIGITAL", "CONSEIL", "PARTNERS",
             "MULTISERVICES"}
    assert {pole for _, pole, _ in BRANCHES} <= poles
    assert len({slug for slug, _, _ in BRANCHES}) == len(BRANCHES)


def test_shipped_catalogue_files_are_well_formed():
    root = Path(__file__).resolve().parents[1] / "knowledge"
    kb = KnowledgeBase(root)
    produits = [d for d in kb.documents if d.type == "produits"]
    if not produits:
        pytest.skip("catalogue pas encore importé")
    assert all(d.pole and d.meta.get("source") for d in produits)
    assert all("rate:" not in d.body for d in produits)
    assert kb.faq == []                      # un import ne rend jamais une FAQ active


# ------------------------------------------------------------------ formations du site
COURSE_PAGE = ('<html><script type="application/ld+json">{"@context":"https://schema.org",'
               '"@type":"BreadcrumbList"}</script><script type="application/ld+json">'
               '{"@type":"Course","name":"Accélérer sa startup","description":"Formation certifiante",'
               '"teaches":"Entrepreneuriat","offers":{"@type":"Offer","price":280000,'
               '"priceCurrency":"XOF"}}</script></html>')


def fake_api_with_site():
    sitemap = ("<urlset>"
               "<url><loc>https://ibig-eduform.com/formation/daf</loc></url>"          # déjà connue
               "<url><loc>https://ibig-eduform.com/formation/accelerer-startup</loc></url>"
               "<url><loc>https://ibig-eduform.com/formation/cassee</loc></url>"
               "<url><loc>https://ibig-eduform.com/a-propos.php</loc></url></urlset>")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ibig-eduform.com":
            path = request.url.path
            if path == "/sitemap.xml":
                return httpx.Response(200, text=sitemap)
            if path == "/formation/accelerer-startup":
                return httpx.Response(200, text=COURSE_PAGE)
            return httpx.Response(500, text="erreur")
        path = request.url.path
        if path == "/api/catalogue":
            return httpx.Response(200, json={"ok": True, "formations": EDU})
        if path == "/api/catalogue/ibig-soft":
            return httpx.Response(200, json={"branch": "ibig-soft", "products": SOFT})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_site_formations_missing_from_partners_are_added(tmp_path):
    result, _ = import_catalog(tmp_path, fake_api_with_site(), today="2026-10-30", delay=0)
    assert (result.site_added, result.site_failed) == (1, 1)    # 1 lue, 1 page illisible
    assert result.counts["IBIG EDUFORM"] == 4                   # 3 de PARTNERS + 1 du site
    text = (tmp_path / "produits" / "eduform-entrepreneuriat.md").read_text(encoding="utf-8")
    assert "### Accélérer sa startup" in text and "- Prix : 280 000 FCFA" in text
    assert "Domaine (indiqué par le site) : Entrepreneuriat" in text
    assert "absente du catalogue IBIG PARTNERS" in text
    daf = (tmp_path / "produits" / "eduform-direction-administration.md").read_text(encoding="utf-8")
    assert "page de la formation sur ibig-eduform.com" not in daf   # connue de PARTNERS : inchangée


def test_site_outage_never_blocks_the_partners_import(tmp_path):
    result, _ = import_catalog(tmp_path, fake_api(), today="2026-10-30", delay=0)  # site : 404
    assert result.site_added == 0 and result.counts["IBIG EDUFORM"] == 3
    off, _ = import_catalog(tmp_path / "x", fake_api_with_site(), delay=0, with_site=False)
    assert off.site_added == 0


# ------------------------------------------------------------------ mise à jour automatique
def test_a_shrunken_catalogue_never_overwrites_the_previous_one(tmp_path):
    import_catalog(tmp_path, fake_api(), today="2026-10-30")
    before = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "produits").glob("*.md")}
    tiny = fake_api()

    def handler(request: httpx.Request) -> httpx.Response:    # l'API ne renvoie plus qu'un produit
        if request.url.path == "/api/catalogue/ibig-soft":
            return httpx.Response(200, json={"products": SOFT[:1]})
        return httpx.Response(404, json={})

    tiny = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CatalogError, match="anormalement réduit"):
        import_catalog(tmp_path, tiny, today="2026-11-06", delay=0)
    assert {p.name: p.read_text(encoding="utf-8")
            for p in (tmp_path / "produits").glob("*.md")} == before


def test_weekly_refresh_reloads_the_knowledge_base_only_when_something_changed(
        rt, tmp_path, monkeypatch):
    import ibig_agent.catalog_import as module
    from ibig_agent.scheduler import job_specs

    real = module.import_catalog
    monkeypatch.setattr(module, "import_catalog",
                        lambda root, **kw: real(root, fake_api(), today="2026-10-30", delay=0))
    rt.settings.catalog_dir = tmp_path
    assert "catalog_refresh" in {j.id for j in job_specs(rt)}
    job = next(j for j in job_specs(rt) if j.id == "catalog_refresh")
    assert job.trigger == "cron" and job.options["day_of_week"] == "sun"
    first = rt.refresh_catalog()
    assert "5 produits" in first and "'ecrits'" in first
    assert any(d.type == "produits" for d in rt.kb.documents)       # base rechargée
    assert "'ecrits': 0" in rt.refresh_catalog()                    # rien de neuf : pas de réécriture
