"""Lot 5 — paiements confirmés à deux personnes, commissions par pôle, « payé / livré »."""

import pytest
from sqlalchemy import select

from ibig_agent.auth import UserStore
from ibig_agent.db import CatalogOffer, Commission, JournalEntry, Payment, Prospect
from ibig_agent.finance import FinanceError


@pytest.fixture
def two_managers(rt):
    users = UserStore(rt.sessions, rt.org)
    users.create("a@ibig.test", "Awa", "direction", "mot-de-passe-solide")
    users.create("b@ibig.test", "Brice", "direction", "mot-de-passe-solide")


@pytest.fixture
def accepted(rt):
    """Un devis de 200 000 FCFA accepté, pôle SOFT."""
    with rt.sessions() as s:
        s.add_all([CatalogOffer(code="x", name="Offre", pole="SOFT", price=200000,
                                prices=[200000], status="actif"),
                   Prospect(email="c@societe.ci", name="Client", pole="SOFT",
                            assigned_to="Koné")])
        s.commit()
        pid = s.scalar(select(Prospect.id))
    qs = rt.quotes
    qid = qs.create(pid, "Awa")
    qs.add_line(qid, "Awa", offer_code="x")
    qs.validate(qid, "Awa")
    qs.mark_sent(qid, "Awa")
    qs.decide(qid, True, "Awa")
    return qid, pid


def test_payment_needs_reference_proof_and_an_accepted_quote(rt, accepted):
    qid, _ = accepted
    f = rt.finance
    for args, msg in (((qid, 0, "virement", "R1", "preuve"), "positif"),
                      ((qid, 100, "troc", "R1", "preuve"), "Mode"),
                      ((qid, 100, "virement", "", "preuve"), "référence"),
                      ((qid, 100, "virement", "R1", ""), "preuve"),
                      ((qid, 300000, "virement", "R1", "preuve"), "dépasse"),
                      ((999, 100, "virement", "R1", "preuve"), "introuvable")):
        with pytest.raises(FinanceError, match=msg):
            f.record(*args, "Awa")
    f.record(qid, 100000, "virement", "R1", "relevé du 30/10", "Awa")
    with pytest.raises(FinanceError, match="double"):
        f.record(qid, 100000, "virement", "R1", "autre preuve", "Awa")
    f.record(qid, 100000, "mobile_money", "R1", "même référence, autre mode", "Awa")


def test_a_second_person_must_confirm_when_there_are_several_managers(rt, accepted, two_managers):
    qid, _ = accepted
    pid = rt.finance.record(qid, 50000, "virement", "R2", "relevé", "Awa")
    with pytest.raises(FinanceError, match="autre personne"):
        rt.finance.confirm(pid, "Awa")
    rt.finance.confirm(pid, "Brice")
    with pytest.raises(FinanceError, match="déjà traité"):
        rt.finance.confirm(pid, "Brice")


def test_sole_manager_may_confirm_and_it_is_journaled(rt, accepted):
    qid, _ = accepted
    pid = rt.finance.record(qid, 50000, "virement", "R3", "relevé", "Awa")
    rt.finance.confirm(pid, "Awa")
    with rt.sessions() as s:
        entry = s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "paiement.confirme")).one()
    assert entry.details["meme_personne"] is True


def test_full_payment_sets_the_reserved_stage_and_closes_the_quote(rt, accepted, two_managers):
    qid, prospect_id = accepted
    f = rt.finance
    first = f.record(qid, 150000, "mobile_money", "T1", "SMS T1", "Awa")
    f.confirm(first, "Brice")
    with rt.sessions() as s:
        assert s.get(Prospect, prospect_id).status == "gagne"      # pas encore payé en totalité
    second = f.record(qid, 50000, "especes", "REÇU-9", "reçu papier n° 9", "Awa")
    with pytest.raises(FinanceError, match="dépasse"):
        f.record(qid, 60000, "especes", "REÇU-10", "reçu", "Awa")
    f.confirm(second, "Brice")
    with rt.sessions() as s:
        assert s.get(Prospect, prospect_id).status == "paye_livre"
    assert rt.quotes.get(qid)[0].status == "paye"
    with pytest.raises(ValueError, match="confirmation financière"):
        rt.commercial.set_stage(prospect_id, "paye_livre", "Awa")   # toujours refusé à la main


def test_rejected_payments_do_not_count(rt, accepted):
    qid, _ = accepted
    pid = rt.finance.record(qid, 80000, "virement", "R4", "relevé", "Awa")
    with pytest.raises(FinanceError, match="motif"):
        rt.finance.reject(pid, "", "Brice")
    rt.finance.reject(pid, "montant non reçu", "Brice")
    s = rt.finance.summary()
    assert s["total"] == 0 and s["outstanding"] == 200000 and s["to_confirm"] == 0


def test_commission_uses_the_pole_rate_frozen_at_payment(rt, accepted, two_managers):
    qid, _ = accepted
    f = rt.finance
    with pytest.raises(FinanceError, match="Pôle inconnu"):
        f.set_rate("NULLE", 5, "Awa")
    with pytest.raises(FinanceError, match="entre 0 et 50"):
        f.set_rate("SOFT", 80, "Awa")
    pid = f.record(qid, 100000, "virement", "R5", "relevé", "Awa")
    assert "Aucun taux" in f.confirm(pid, "Brice")                  # taux absent : pas de commission
    f.set_rate("SOFT", 2.5, "Awa")
    pid2 = f.record(qid, 100000, "virement", "R6", "relevé", "Awa")
    assert "2500" in f.confirm(pid2, "Brice")
    f.set_rate("SOFT", 10, "Awa")                                   # un changement n'altère rien
    with rt.sessions() as s:
        c = s.scalar(select(Commission))
    assert (c.base, c.rate_bp, c.amount, c.beneficiary, c.status) == (
        100000, 250, 2500, "Koné", "a_valider")
    f.commission_step(c.id, "valider", "Awa")
    with pytest.raises(FinanceError, match="autre personne"):
        f.commission_step(c.id, "verser", "Awa")
    f.commission_step(c.id, "verser", "Brice")
    with pytest.raises(FinanceError, match="impossible"):
        f.commission_step(c.id, "annuler", "Brice")
    assert f.summary()["commissions"] == {"verse": 2500}


def test_finance_pages(rt, accepted, two_managers):
    from fastapi.testclient import TestClient

    from ibig_agent.dashboard.app import create_app

    qid, _ = accepted
    UserStore(rt.sessions, rt.org).create("val@ibig.test", "Valideur", "valideur",
                                          "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    awa, brice = client("a@ibig.test"), client("b@ibig.test")
    assert "Enregistrer un paiement" in awa.get(f"/devis/{qid}").text
    sent = awa.post(f"/finance/devis/{qid}/paiement", follow_redirects=False,
                    data={"montant": "200000", "mode": "virement", "reference": "R9",
                          "preuve": "relevé"})
    assert "/finance" in sent.headers["location"]
    page = brice.get("/finance").text
    assert "R9" in page and "Confirmer" in page
    with rt.sessions() as s:
        pay_id = s.scalar(select(Payment.id))
    brice.post(f"/finance/paiement/{pay_id}/confirmer")
    brice.post("/finance/taux", data={"pole": "SOFT", "taux": "5"})
    assert "200 000" in brice.get("/finance").text
    assert brice.post("/finance/paiement/1/inconnue", follow_redirects=False).status_code == 404
    assert "Payé" in brice.get("/devis").text
    assert client("val@ibig.test").get("/finance").status_code == 403
