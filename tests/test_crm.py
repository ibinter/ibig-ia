"""Lot 1 — CRM : étapes du pipeline, origine, affectation, fil d'activités, fiche prospect."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ibig_agent.agents.commercial import ACTIVE, CLOSED, HUMAN_STAGES, STAGES
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JournalEntry, Prospect
from ibig_agent.privacy import CLOSED_PROSPECTS

PASSWORD = "mot-de-passe-solide"


@pytest.fixture
def prospect(rt):
    with rt.sessions() as s:
        p = Prospect(email="ecole@exemple.ci", name="École Les Palmiers", pole="SOFT",
                     status="qualifie", need="Gestion scolaire", score=80, temperature="chaud")
        s.add(p)
        s.commit()
        return p.id


def activities(rt, pid):
    return rt.commercial.activities(pid)


def test_pipeline_stages_are_human_managed_and_never_followed_up_automatically(rt, prospect):
    assert not set(HUMAN_STAGES) & set(ACTIVE)        # devis, démo… : pas de relance automatique
    assert {"injoignable", "invalide", "exclu"} <= set(CLOSED)
    assert {"interesse", "demo", "devis", "negociation"} <= set(STAGES)
    assert "paye_livre" not in STAGES                  # réservé à une confirmation financière
    assert not {"exclu", "injoignable", "invalide"} & set(CLOSED_PROSPECTS)  # jamais purgés


def test_stage_change_is_dated_attributed_and_logged(rt, prospect):
    rt.commercial.set_stage(prospect, "devis", "Awa <awa@ibig.test>", "devis n° 12 envoyé")
    with rt.sessions() as s:
        p = s.get(Prospect, prospect)
        journal = s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "prospect.etape")).one()
    assert p.status == "devis"
    first = activities(rt, prospect)[0]
    assert first.kind == "etape" and first.by == "Awa <awa@ibig.test>"
    assert "Qualifié → Devis envoyé" in first.summary and "devis n° 12" in first.summary
    assert journal.decided_by == "Awa <awa@ibig.test>" and journal.details["etape"] == "devis"


def test_paid_stage_cannot_be_set_by_hand_and_unknown_stages_are_refused(rt, prospect):
    with pytest.raises(ValueError, match="confirmation financière"):
        rt.commercial.set_stage(prospect, "paye_livre", "Awa")
    with pytest.raises(ValueError, match="inconnue"):
        rt.commercial.set_stage(prospect, "n_importe_quoi", "Awa")
    with pytest.raises(ValueError, match="introuvable"):
        rt.commercial.set_stage(9999, "devis", "Awa")
    with rt.sessions() as s:
        assert s.get(Prospect, prospect).status == "qualifie"      # rien n'a bougé
    assert activities(rt, prospect) == []


def test_excluded_prospect_is_never_followed_up(rt, prospect):
    rt.commercial.set_stage(prospect, "exclu", "Awa", "demande de ne plus être contacté")
    with rt.sessions() as s:
        p = s.get(Prospect, prospect)
    assert p.status == "exclu" and p.stop_followups is True


def test_notes_assignment_and_origin(rt, prospect):
    rt.commercial.add_note(prospect, "Rappeler jeudi, directeur absent", "Awa")
    with pytest.raises(ValueError, match="vide"):
        rt.commercial.add_note(prospect, "   ", "Awa")
    rt.commercial.assign(prospect, "commercial", "kofi@ibig.test", "Direction")
    rt.commercial.set_origin(prospect, "https://ecole-palmiers.ci/contact", "verifie", "Awa")
    with pytest.raises(ValueError, match="adresse web"):
        rt.commercial.set_origin(prospect, "javascript:alert(1)", "verifie", "Awa")
    with pytest.raises(ValueError, match="Vérification inconnue"):
        rt.commercial.set_origin(prospect, "https://x.ci", "peut-etre", "Awa")
    with rt.sessions() as s:
        p = s.get(Prospect, prospect)
    assert (p.assigned_agent, p.assigned_to) == ("commercial", "kofi@ibig.test")
    assert p.source_url == "https://ecole-palmiers.ci/contact" and p.verification == "verifie"
    assert p.collected_at is not None                       # date de collecte posée à l'origine
    kinds = [a.kind for a in activities(rt, prospect)]
    assert kinds.count("note") == 1 and kinds.count("systeme") == 2
    assert activities(rt, prospect)[0].summary.startswith("Origine")   # plus récent d'abord


def test_csv_import_creates_the_first_activity(rt, tmp_path):
    f = tmp_path / "essais.csv"
    f.write_text("email,nom,pole,solution\nnouveau@exemple.ci,Cabinet X,SOFT,STOCKFLOW\n",
                 encoding="utf-8")
    result = rt.commercial.import_csv(f)
    assert result.crees == 1
    with rt.sessions() as s:
        pid = s.scalar(select(Prospect.id).where(Prospect.email == "nouveau@exemple.ci"))
    assert "import" in activities(rt, pid)[0].summary


@pytest.fixture
def web(rt):
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", PASSWORD)
    users.create("val@ibig.test", "Valideur", "valideur", PASSWORD)

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": PASSWORD})
        return c

    return client


def test_prospect_page_and_actions_for_the_direction(rt, prospect, web):
    dg = web("dg@ibig.test")
    page = dg.get(f"/prospects/{prospect}").text
    assert "École Les Palmiers" in page and "Informations manquantes" in page
    assert "origine (adresse de la source)" in page      # donnée manquante signalée
    dg.post(f"/prospects/{prospect}/etape", data={"etape": "demo", "note": "mardi 10 h"})
    dg.post(f"/prospects/{prospect}/note", data={"texte": "Contact : le directeur"})
    dg.post(f"/prospects/{prospect}/affectation", data={"agent": "commercial",
                                                          "commercial": "kofi@ibig.test"})
    refused = dg.post(f"/prospects/{prospect}/etape", data={"etape": "paye_livre"},
                      follow_redirects=False)
    assert "Refus" in refused.headers["location"]
    page = dg.get(f"/prospects/{prospect}").text
    assert "Démo / rendez-vous" in page and "mardi 10 h" in page and "kofi@ibig.test" in page
    assert "Qualifié → Démo / rendez-vous" in page and "Contact : le directeur" in page
    board = dg.get("/prospects").text
    assert "Démo, devis, négociation" in board and f"/prospects/{prospect}" in board


def test_a_validator_only_sees_the_prospects_of_their_poles(rt, prospect, web):
    val = web("val@ibig.test")                            # aucun pôle attribué à ce valideur
    assert val.get(f"/prospects/{prospect}").status_code == 404
    assert val.post(f"/prospects/{prospect}/etape", data={"etape": "perdu"},
                    follow_redirects=False).status_code == 404
    assert val.post(f"/prospects/{prospect}/affectation", data={"agent": "x"},
                    follow_redirects=False).status_code in (403, 404)
    with rt.sessions() as s:
        assert s.get(Prospect, prospect).status == "qualifie"
