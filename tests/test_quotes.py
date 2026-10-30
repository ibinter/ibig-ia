"""Lot 4 — devis : prix du catalogue, validation, cycle de vie, conversion, pages, boîte unifiée."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from ibig_agent.db import CatalogOffer, JournalEntry, ProcessedMessage, Prospect, Quote, utcnow
from ibig_agent.quotes import QuoteError


@pytest.fixture
def web(rt):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("val@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    return client


@pytest.fixture
def data(rt):
    with rt.sessions() as s:
        s.add_all([
            CatalogOffer(code="soft-erp", name="ERP Compta", pole="SOFT", price=150000,
                         prices=[150000], status="actif"),
            CatalogOffer(code="soft-multi", name="Offre à deux tarifs", pole="SOFT", price=1000,
                         prices=[1000, 2000], status="actif"),
            CatalogOffer(code="soft-devis", name="Sur mesure", pole="SOFT", price=None,
                         prices=[], status="sur_devis"),
            Prospect(email="client@societe.ci", name="Société Client", pole="SOFT")])
        s.commit()
        return s.scalar(select(Prospect.id))


def test_catalogue_price_is_used_and_manual_price_is_flagged(rt, data):
    qs = rt.quotes
    qid = qs.create(data, "Awa")
    qs.add_line(qid, "Awa", offer_code="soft-erp", qty=2)
    qs.add_line(qid, "Awa", label="Installation", price=50000)
    q, lines = qs.get(qid)
    assert q.number.startswith("DV-") and q.total == 350000
    assert [ln.price_source for ln in lines] == ["catalogue", "manuel"]
    for code in ("soft-multi", "soft-devis"):      # tarif ambigu ou absent : pas de prix inventé
        with pytest.raises(QuoteError, match="tarif à confirmer"):
            qs.add_line(qid, "Awa", offer_code=code)
    qs.add_line(qid, "Awa", offer_code="soft-devis", price=80000)
    assert qs.get(qid)[0].total == 430000
    with pytest.raises(QuoteError, match="inconnue"):
        qs.add_line(qid, "Awa", offer_code="nulle")
    with pytest.raises(QuoteError, match="Quantité"):
        qs.add_line(qid, "Awa", offer_code="soft-erp", qty=0)


def test_numbers_increase_and_empty_quote_cannot_be_validated(rt, data):
    a, b = rt.quotes.create(data, "Awa"), rt.quotes.create(data, "Awa")
    assert rt.quotes.get(b)[0].number.endswith("0002")
    with pytest.raises(QuoteError, match="au moins une ligne"):
        rt.quotes.validate(a, "Awa")


def test_full_cycle_moves_the_prospect_and_is_journaled(rt, data):
    qs = rt.quotes
    qid = qs.create(data, "Awa")
    qs.add_line(qid, "Awa", offer_code="soft-erp")
    with pytest.raises(QuoteError, match="validé"):
        qs.mark_sent(qid, "Awa")
    qs.validate(qid, "Awa")
    with pytest.raises(QuoteError, match="brouillon"):
        qs.add_line(qid, "Awa", label="Tard", price=1)
    qs.mark_sent(qid, "Awa")
    with rt.sessions() as s:
        assert s.get(Prospect, data).status == "devis"
    qs.decide(qid, True, "Awa")
    with rt.sessions() as s:
        assert s.get(Prospect, data).status == "gagne"
        actions = {j.action_type for j in s.scalars(select(JournalEntry)).all()}
    assert {"devis.cree", "devis.valide", "devis.envoye", "devis.accepte"} <= actions
    pipe = qs.pipeline()
    assert pipe["accepte"]["total"] == 150000 and pipe["_conversion"]["pct"] == 100
    with pytest.raises(QuoteError, match="clos"):
        qs.cancel(qid, "Awa")


def test_refusal_marks_the_prospect_lost(rt, data):
    qs = rt.quotes
    qid = qs.create(data, "Awa")
    qs.add_line(qid, "Awa", offer_code="soft-erp")
    qs.validate(qid, "Awa")
    qs.mark_sent(qid, "Awa")
    qs.decide(qid, False, "Awa")
    with rt.sessions() as s:
        assert s.get(Prospect, data).status == "perdu"


def test_validation_refuses_when_the_catalogue_price_changed(rt, data):
    qs = rt.quotes
    qid = qs.create(data, "Awa")
    qs.add_line(qid, "Awa", offer_code="soft-erp")
    with rt.sessions() as s:
        s.scalar(select(CatalogOffer).where(CatalogOffer.code == "soft-erp")).price = 175000
        s.commit()
    with pytest.raises(QuoteError, match="a changé"):
        qs.validate(qid, "Awa")


def test_expired_quotes_are_closed(rt, data):
    qs = rt.quotes
    qid = qs.create(data, "Awa")
    qs.add_line(qid, "Awa", offer_code="soft-erp")
    qs.validate(qid, "Awa", days=5)
    assert qs.expire_due() == 0
    assert qs.expire_due(utcnow() + timedelta(days=6)) == 1
    assert qs.get(qid)[0].status == "expire"


def test_pages_and_unified_inbox(rt, web, data):
    with rt.sessions() as s:
        s.add(ProcessedMessage(mailbox="contact@ibig.test", message_id="m1", sender="x@y.ci",
                               subject="Demande de devis ERP", pole="SOFT", category="prospect",
                               decision="prospect"))
        s.commit()
    dg = web("dg@ibig.test")
    made = dg.post(f"/prospects/{data}/devis", follow_redirects=False)
    qid = int(made.headers["location"].split("/devis/")[1].split("?")[0])
    assert "DV-" in dg.get(f"/devis/{qid}", params={"q": "ERP"}).text
    dg.post(f"/devis/{qid}/ligne", data={"code": "soft-erp", "quantite": "1"})
    assert "ERP Compta" in dg.get(f"/devis/{qid}").text
    early = dg.get(f"/devis/{qid}/imprimer", follow_redirects=False)
    assert early.status_code == 303                       # pas d'impression avant validation
    dg.post(f"/devis/{qid}/valider", data={"jours": "30"})
    assert "150 000" in dg.get(f"/devis/{qid}/imprimer").text
    dg.post(f"/devis/{qid}/envoyer")
    dg.post(f"/devis/{qid}/accepter")
    assert "Accepté" in dg.get("/devis").text
    with rt.sessions() as s:
        assert s.scalar(select(Quote.status)) == "accepte"
    assert dg.post(f"/devis/{qid}/inconnue", follow_redirects=False).status_code == 404
    assert "Demande de devis ERP" in dg.get("/boite", params={"q": "devis"}).text
    assert "Demande de devis ERP" not in dg.get("/boite", params={"boite": "autre"}).text
    val = web("val@ibig.test")
    assert val.get("/devis").status_code == 403 and val.get("/boite").status_code == 403


def test_home_shows_the_sales_summary_to_direction_only(rt, web, data):
    qs = rt.quotes
    qid = qs.create(data, "Awa")
    qs.add_line(qid, "Awa", offer_code="soft-erp")
    qs.validate(qid, "Awa")
    qs.mark_sent(qid, "Awa")
    home = web("dg@ibig.test").get("/").text
    assert "devis en attente du client" in home and "restant à encaisser" in home
    assert "devis en attente du client" not in web("val@ibig.test").get("/").text
