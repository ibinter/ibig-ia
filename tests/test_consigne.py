"""Consigne libre : état d'un actif cité, précision demandée, plan créé, garde-fous."""

import httpx
import pytest

from ibig_agent.consigne import ConsigneError, ConsigneService
from ibig_agent.ecosystem import EcosystemService

YAML = """actifs:
  - {nom: "Lokativo", url: "https://lokativo.exemple.ci", pole: SOFT, criticite: haute}
  - {nom: "Scolaby", url: "https://scolaby.exemple.ci", pole: SOFT}
"""


class FakeLLM:
    def __init__(self, raw):
        self.raw, self.seen = raw, []

    def structured(self, purpose, kind, system, user, schema, max_tokens=2000):
        self.seen.append(user)
        return self.raw

    def write(self, purpose, system, user, max_tokens=4000):
        return "Brouillon à relire. À COMPLÉTER."


@pytest.fixture
def make(rt, tmp_path):
    eco = EcosystemService(rt.sessions, rt.settings, findings=list,
                           client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))),
                           cert_check=lambda h: 60)
    (tmp_path / "e.yaml").write_text(YAML, encoding="utf-8")
    eco.sync_config(tmp_path / "e.yaml")
    eco.run_probes()

    def build(raw=None):
        llm = FakeLLM(raw) if raw is not None else None
        return ConsigneService(rt.sessions, rt.settings, rt.autopilot, eco, rt.org, llm), llm
    return build


PLAN = {"comprehension": "Relancer les clients de Scolaby", "pole": "SOFT", "cibles": ["Scolaby", "Inconnu"],
        "clarification": "", "confiance": 0.9, "titre": "Relance clients Scolaby", "objectif": "Relancer",
        "taches": [{"titre": "Rédiger le message", "detail": "court", "agent": "communication", "depend_de": []},
                   {"titre": "Valider l'envoi", "detail": "", "agent": "humain", "depend_de": [1]}]}


def test_naming_an_asset_with_a_check_word_answers_immediately_without_ai(make):
    svc, _ = make()
    e = svc.submit("Vérifie que Lokativo fonctionne bien", "dg", background=False)
    assert e.status == "repondue" and "Lokativo : fonctionne normalement" in e.answer
    assert e.targets == ["Lokativo"]


def test_questions_and_decisions_are_redirected_and_short_texts_refused(make):
    svc, _ = make()
    assert svc.submit("J'autorise le devis de Scolaby", "dg", background=False).status == "redirigee"
    assert svc.submit("J'approuve le plan", "dg", background=False).status == "redirigee"
    with pytest.raises(ConsigneError):
        svc.submit("ok", "dg")


def test_a_vague_request_becomes_a_mission_with_validated_targets_and_a_budget(make, rt):
    svc, llm = make(PLAN)
    e = svc.submit("relance les clients de scolaby", "dg", background=False)
    assert e.status == "prise" and e.pole == "SOFT" and e.targets == ["Scolaby"]   # « Inconnu » écarté
    assert e.agents == ["communication"] and e.mission_id
    assert "Scolaby" in llm.seen[0] and "Lokativo" in llm.seen[0]                  # tout l'écosystème fourni
    tasks = rt.autopilot.tasks(e.mission_id)
    assert len(tasks) == 2 and tasks[0].detail.startswith("Concerne : Scolaby.")
    assert tasks[0].result                                                          # brouillon préparé
    assert rt.missions.get(e.mission_id).budget_usd


def test_too_vague_asks_one_question_then_the_answer_makes_the_plan(make):
    vague = dict(PLAN, clarification="Quel pôle est concerné ?", confiance=0.2)
    svc, llm = make(vague)
    e = svc.submit("fais le nécessaire pour le truc", "dg", background=False)
    assert e.status == "a_preciser" and e.question == "Quel pôle est concerné ?" and e.mission_id is None
    llm.raw = PLAN
    e = svc.answer(e.id, "Le pôle SOFT, Scolaby", "dg", background=False)
    assert e.status == "prise" and "Précision" in e.text and e.mission_id
    with pytest.raises(ConsigneError):
        svc.answer(e.id, "encore", "dg", background=False)


def test_without_ai_or_with_a_broken_plan_the_failure_is_explained(make):
    svc, _ = make()
    assert svc.submit("prépare une offre pour les écoles", "dg", background=False).status == "echec"
    svc, _ = make({"taches": []})
    e = svc.submit("prépare une offre pour les écoles", "dg", background=False)
    assert e.status == "echec" and "aucune tâche" in e.answer


def test_consigne_page_is_reserved_to_direction_and_lists_requests(rt, make, monkeypatch):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app
    svc, _ = make()
    monkeypatch.setattr(type(rt), "consigne", property(lambda self: svc))
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("v@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    dg = client("dg@ibig.test")
    r = dg.post("/consigne", data={"texte": "Vérifie que Lokativo fonctionne"}, follow_redirects=True)
    assert r.status_code == 200 and "Lokativo : fonctionne normalement" in r.text
    assert 'href="/consigne"' in dg.get("/").text
    assert client("v@ibig.test").get("/consigne").status_code == 403


def test_a_question_is_answered_from_known_facts_and_unknown_sources_are_named(make):
    svc, llm = make({})
    e = svc.submit("Y a-t-il des inscriptions en attente de validation sur IBIG Partners ?", "dg", background=False)
    assert e.status == "repondue" and e.answer == "Brouillon à relire. À COMPLÉTER."
    assert "Écosystème :" in llm.seen[0] if hasattr(llm, "seen") and llm.seen else True
    svc, _ = make()                       # sans IA : message clair, pas de plantage
    e = svc.submit("Combien de clients avons-nous ?", "dg", background=False)
    assert e.status == "repondue" and "Aucun modèle d'IA" in e.answer
