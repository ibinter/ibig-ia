"""Registre du catalogue (Lot 1) : version, statut, validation, historique des tarifs."""

import httpx
import pytest
from sqlalchemy import select

from ibig_agent.catalog_import import CatalogError, import_catalog
from ibig_agent.catalog_store import (
    IMPORT_BY,
    groups_from_db,
    list_offers,
    set_price,
    set_status,
    status_counts,
)
from ibig_agent.db import CatalogOffer, CatalogPriceHistory


class Source:
    """API PARTNERS simulée, modifiable entre deux imports."""

    def __init__(self):
        self.soft = [
            {"slug": "a", "name": "AGRIFRIK — Basique", "pricingType": "MONTHLY_SUB",
             "price": 6500, "rate": 20, "siteUrl": "https://agrifrik.ibigsoft.com",
             "description": "ERP agricole."},
            {"slug": "b", "name": "Scolaby Pro", "pricingType": "MONTHLY_SUB", "price": 20000,
             "rate": 20, "siteUrl": "https://scolaby.com", "description": ""},
        ]
        self.edu = [
            {"name": "IA pour Professionnels", "price": 95000, "rate": 10, "category": "IA",
             "siteUrl": "https://ibig-eduform.com/formation/ia", "description": ""},
            {"name": "IA pour Professionnels", "price": 50000, "rate": 10, "category": "IA",
             "siteUrl": "https://ibig-eduform.com/formation/ia2", "description": ""},
        ]

    def client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host != "ibigpartners.com":
                return httpx.Response(404, json={})
            if request.url.path == "/api/catalogue":
                return httpx.Response(200, json={"formations": self.edu})
            if request.url.path == "/api/catalogue/ibig-soft":
                return httpx.Response(200, json={"products": self.soft})
            return httpx.Response(404, json={})

        return httpx.Client(transport=httpx.MockTransport(handler))


def run(rt, src, tmp_path, min_ratio=0.0, **kw):
    # Jeux de données minuscules : la garde « catalogue réduit » a son propre test
    return import_catalog(tmp_path, src.client(), today="2026-10-30", delay=0,
                          sessions=rt.sessions, min_ratio=min_ratio, **kw)


def offers(rt):
    with rt.sessions() as s:
        return {o.name: o for o in s.scalars(select(CatalogOffer)).all()}


def text(tmp_path):
    return "\n".join(p.read_text(encoding="utf-8") for p in (tmp_path / "produits").glob("*.md"))


def test_first_import_creates_versioned_validated_offers(rt, tmp_path):
    result, _ = run(rt, Source(), tmp_path)
    assert result.register["crees"] == 3
    o = offers(rt)
    agri, ia = o["AGRIFRIK — Basique"], o["IA pour Professionnels"]
    assert (agri.status, agri.version, agri.price) == ("actif", 1, 6500)
    assert agri.validated_by.startswith(IMPORT_BY) and agri.validated_at is not None
    assert agri.code == "ibig-soft:agrifrik basique"            # identifiant stable
    assert (ia.status, ia.prices, ia.validated_by) == ("a_confirmer", [50000, 95000], "")
    with rt.sessions() as s:
        assert s.query(CatalogPriceHistory).count() == 3        # une ligne de départ par offre
    assert "rate" not in text(tmp_path).lower()                 # commission jamais recopiée


def test_unchanged_reimport_touches_nothing(rt, tmp_path):
    src = Source()
    run(rt, src, tmp_path)
    result, stats = run(rt, src, tmp_path)
    assert result.register == {"crees": 0, "tarifs_modifies": 0, "mises_a_jour": 0,
                               "archives": 0, "reactives": 0, "inchanges": 3}
    assert stats["ecrits"] == 0
    assert offers(rt)["AGRIFRIK — Basique"].version == 1


def test_price_change_bumps_version_and_keeps_history(rt, tmp_path):
    src = Source()
    run(rt, src, tmp_path)
    src.soft[0]["price"] = 7000
    result, _ = run(rt, src, tmp_path)
    agri = offers(rt)["AGRIFRIK — Basique"]
    assert result.register["tarifs_modifies"] == 1
    assert (agri.price, agri.version, agri.status) == (7000, 2, "actif")  # géré par l'import
    with rt.sessions() as s:
        rows = s.scalars(select(CatalogPriceHistory).where(
            CatalogPriceHistory.offer_id == agri.id).order_by(CatalogPriceHistory.id)).all()
    assert [r.price for r in rows] == [6500, 7000]
    assert "7 000 FCFA par mois" in text(tmp_path)


def test_a_human_decision_is_never_overwritten_by_the_import(rt, tmp_path):
    src = Source()
    run(rt, src, tmp_path)
    scola = offers(rt)["Scolaby Pro"]
    assert set_status(rt.sessions, scola.id, "suspendu", "Awa <awa@ibig.test>")[0]
    run(rt, src, tmp_path)
    assert "Scolaby Pro" not in text(tmp_path)                    # suspendue : absente des fichiers
    assert offers(rt)["Scolaby Pro"].status == "suspendu"          # et le reste malgré l'import
    # Tarif validé par un humain qui change à la source : l'offre repasse « à confirmer »
    agri = offers(rt)["AGRIFRIK — Basique"]
    set_status(rt.sessions, agri.id, "actif", "Awa <awa@ibig.test>")
    src.soft[0]["price"] = 9000
    run(rt, src, tmp_path)
    agri = offers(rt)["AGRIFRIK — Basique"]
    assert agri.status == "a_confirmer" and agri.validated_by.startswith("Awa")
    assert (agri.prices, agri.source_prices, agri.version) == ([6500], [9000], 3)
    # le tarif validé reste affiché, signalé « à confirmer » ; la source est gardée pour la revue
    assert "6 500 FCFA par mois (tarif à confirmer avec l'équipe)" in text(tmp_path)


def test_ambiguous_price_needs_a_human_choice(rt, tmp_path):
    run(rt, Source(), tmp_path)
    ia = offers(rt)["IA pour Professionnels"]
    ok, why = set_status(rt.sessions, ia.id, "actif", "Awa")
    assert not ok and "tarif unique" in why
    assert set_price(rt.sessions, ia.id, 95000, "Awa")[0]
    ia = offers(rt)["IA pour Professionnels"]
    assert (ia.status, ia.price, ia.version) == ("actif", 95000, 2)
    run(rt, Source(), tmp_path)           # la source reste ambiguë : la décision humaine tient
    ia = offers(rt)["IA pour Professionnels"]
    assert (ia.status, ia.price, ia.version, ia.prices) == ("actif", 95000, 2, [95000])
    assert not set_price(rt.sessions, ia.id, 0, "Awa")[0]
    assert set_status(rt.sessions, ia.id, "sur_devis", "Awa")[0]
    run(rt, Source(), tmp_path)
    assert "sur devis : prix à demander à l'équipe" in text(tmp_path)
    assert not set_status(rt.sessions, ia.id, "inconnu", "Awa")[0]


def test_missing_offer_is_archived_never_deleted_and_comes_back(rt, tmp_path):
    src = Source()
    run(rt, src, tmp_path)
    gone = src.soft.pop(1)
    result, _ = run(rt, src, tmp_path)
    assert result.register["archives"] == 1
    assert offers(rt)["Scolaby Pro"].status == "archive"           # conservée, avec son historique
    assert "Scolaby Pro" not in text(tmp_path)
    src.soft.append(gone)
    result, _ = run(rt, src, tmp_path)
    assert result.register["reactives"] == 1
    assert offers(rt)["Scolaby Pro"].status == "actif" and "Scolaby Pro" in text(tmp_path)


def test_a_drained_source_never_archives_anything(rt, tmp_path):
    src = Source()
    run(rt, src, tmp_path)
    src.soft, src.edu = [], []                                     # l'API ne renvoie plus rien
    with pytest.raises(CatalogError):
        run(rt, src, tmp_path)
    assert {o.status for o in offers(rt).values()} <= {"actif", "a_confirmer"}
    src.edu = Source().edu                                         # seule la marque SOFT disparaît
    run(rt, src, tmp_path)
    assert offers(rt)["Scolaby Pro"].status == "actif"             # marque non lue : pas archivée


def test_listing_and_counts_for_the_dashboard(rt, tmp_path):
    run(rt, Source(), tmp_path)
    rows, total = list_offers(rt.sessions, q="agrifrik")
    assert total == 1 and rows[0].name == "AGRIFRIK — Basique"
    assert list_offers(rt.sessions, status="a_confirmer")[1] == 1
    assert list_offers(rt.sessions, pole="SOFT")[1] == 2
    assert status_counts(rt.sessions) == {"actif": 2, "a_confirmer": 1}
    assert {g.brand for g in groups_from_db(rt.sessions)} == {"IBIG SOFT", "IBIG EDUFORM"}


# ------------------------------------------------------------------ page du tableau de bord
@pytest.fixture
def web(rt, tmp_path):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    rt.settings.knowledge_dir = tmp_path
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("val@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    return client


def test_catalogue_page_lists_offers_and_applies_decisions_at_once(rt, tmp_path, web):
    run(rt, Source(), tmp_path)
    dg = web("dg@ibig.test")
    page = dg.get("/catalogue").text
    assert "AGRIFRIK — Basique" in page and "IA pour Professionnels" in page
    assert "50 000 / 95 000 FCFA" in page and "À confirmer" in page
    assert "AGRIFRIK" in dg.get("/catalogue", params={"q": "agrifrik"}).text
    assert "Scolaby" not in dg.get("/catalogue", params={"q": "agrifrik"}).text
    scola = offers(rt)["Scolaby Pro"]
    assert "Scolaby Pro" in text(tmp_path)
    dg.post(f"/catalogue/{scola.id}/statut", data={"statut": "indisponible"})
    assert "Scolaby Pro" not in text(tmp_path)          # fichiers de l'agent régénérés aussitôt
    assert offers(rt)["Scolaby Pro"].validated_by.startswith("Direction")
    ia = offers(rt)["IA pour Professionnels"]
    refused = dg.post(f"/catalogue/{ia.id}/statut", data={"statut": "actif"},
                      follow_redirects=False)
    assert "Refus" in refused.headers["location"]       # tarif ambigu : choisir d'abord
    dg.post(f"/catalogue/{ia.id}/tarif", data={"prix": "95000"})
    assert "95 000 FCFA" in text(tmp_path) and offers(rt)["IA pour Professionnels"].status == "actif"


def test_a_validator_can_read_but_never_decide(rt, tmp_path, web):
    run(rt, Source(), tmp_path)
    val = web("val@ibig.test")
    assert val.get("/catalogue").status_code == 200
    scola = offers(rt)["Scolaby Pro"]
    assert val.post(f"/catalogue/{scola.id}/statut", data={"statut": "suspendu"},
                    follow_redirects=False).status_code == 403
    assert val.post(f"/catalogue/{scola.id}/tarif", data={"prix": "1"},
                    follow_redirects=False).status_code == 403
    assert offers(rt)["Scolaby Pro"].status == "actif"
