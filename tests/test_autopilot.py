"""Phase 1 — Autopilot : plan validé, tâches avec dépendances, preuves, tentatives, pages."""

import pytest
from sqlalchemy import select

from ibig_agent.autopilot import AutopilotError, validate_plan
from ibig_agent.db import Mission, MissionTask

PLAN = {"titre": "Lancement Excel avancé", "objectif": "Remplir la session de novembre",
        "taches": [
            {"titre": "Rédiger l'annonce", "detail": "", "agent": "communication",
             "depend_de": []},
            {"titre": "Valider l'annonce", "detail": "", "agent": "humain", "depend_de": [1]},
            {"titre": "Préparer la liste de prospects", "detail": "", "agent": "prospecteur",
             "depend_de": []},
            {"titre": "Planifier les relances", "detail": "", "agent": "commercial",
             "depend_de": [2, 3]}]}
INSTRUCTION = "Préparer le lancement de la formation Excel avancé"


@pytest.fixture
def ap(rt, llm):
    llm.autopilot_plan = PLAN
    return rt.autopilot


def test_bad_plans_are_refused():
    for plan, msg in (({"taches": []}, "aucune tâche"),
                      ({"taches": [{"titre": "", "agent": "", "depend_de": []}]}, "titre"),
                      ({"taches": [{"titre": "A", "agent": "", "depend_de": [1]}]}, "postérieure"),
                      ({"taches": [{"titre": "A", "agent": "", "depend_de": [0]}]}, "inexistante"),
                      ({"taches": [{"titre": f"T{i}", "agent": "", "depend_de": []}
                                   for i in range(13)]}, "trop long")):
        with pytest.raises(AutopilotError, match=msg):
            validate_plan(plan)
    _, _, tasks = validate_plan({"taches": [{"titre": "A", "agent": "pirate", "depend_de": []}]})
    assert tasks[0].agent == ""                                # agent inconnu : une personne


def test_instruction_becomes_a_traceable_multi_task_mission(ap, rt):
    with pytest.raises(AutopilotError, match="instruction"):
        ap.plan("court", "Awa")
    mid = ap.plan(INSTRUCTION, "Awa")
    with rt.sessions() as s:
        m = s.get(Mission, mid)
    assert m.kind == "autopilot" and m.status == "en_cours" and m.approved_by == "Awa"
    tasks = ap.tasks(mid)
    assert [t.status for t in tasks] == ["prete", "a_faire", "prete", "a_faire"]
    assert tasks[3].depends_on == [tasks[1].id, tasks[2].id]
    assert len({t.idempotency_key for t in tasks}) == 4
    assert ap.progress(mid) == (0, 4)


def test_dependencies_proofs_and_automatic_closure(ap, rt):
    mid = ap.plan(INSTRUCTION, "Awa")
    t = ap.tasks(mid)
    with pytest.raises(AutopilotError, match="preuve"):
        ap.complete(t[0].id, " ", "Awa")
    with pytest.raises(AutopilotError, match="prête"):
        ap.start(t[1].id, "Awa")
    ap.start(t[0].id, "Awa")
    ap.complete(t[0].id, "annonce n° 12", "Awa")
    assert ap.tasks(mid)[1].status == "prete"
    ap.complete(t[1].id, "validée par la direction", "Awa")
    assert ap.tasks(mid)[3].status == "a_faire"                # attend encore la tâche 3
    ap.complete(t[2].id, "fichier de 40 contacts", "Awa")
    assert ap.tasks(mid)[3].status == "prete"
    ap.complete(t[3].id, "séquence J+3/J+7 créée", "Awa")
    with rt.sessions() as s:
        assert s.get(Mission, mid).status == "termine"
    assert ap.progress(mid) == (4, 4)


def test_failures_retry_then_block_dependants(ap):
    mid = ap.plan(INSTRUCTION, "Awa")
    first = ap.tasks(mid)[0]
    for _ in range(2):
        ap.start(first.id, "Awa")
        assert "nouvelle tentative" in ap.fail(first.id, "service indisponible", "Awa")
    ap.start(first.id, "Awa")
    assert "définitif" in ap.fail(first.id, "toujours indisponible", "Awa")
    states = {t.position: t.status for t in ap.tasks(mid)}
    assert states == {1: "echec", 2: "bloquee", 3: "prete", 4: "bloquee"}
    with pytest.raises(AutopilotError, match="état actuel"):
        ap.complete(first.id, "preuve", "Awa")


def test_cancelling_every_task_closes_the_mission(ap, rt):
    mid = ap.plan(INSTRUCTION, "Awa")
    for t in ap.tasks(mid):
        ap.cancel(t.id, "Awa")
    with rt.sessions() as s:
        assert s.get(Mission, mid).status == "termine"
        assert s.scalar(select(MissionTask.id).where(MissionTask.status == "prete")) is None


def test_autopilot_pages(rt, llm):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    llm.autopilot_plan = PLAN
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("val@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    dg = client("dg@ibig.test")
    assert "Donner une instruction" in dg.get("/missions").text
    bad = dg.post("/autopilot", data={"instruction": "court"}, follow_redirects=False)
    assert "Refus" in bad.headers["location"]
    made = dg.post("/autopilot", data={"instruction": INSTRUCTION, "pole": "SOFT"},
                   follow_redirects=False)
    mid = int(made.headers["location"].split("/missions/")[1].split("?")[0])
    page = dg.get(f"/missions/{mid}").text
    assert "Planifier les relances" in page
    tid = rt.autopilot.tasks(mid)[0].id
    dg.post(f"/missions/{mid}/tache/{tid}/terminer", data={"preuve": "annonce n° 12"})
    assert "annonce n° 12" in dg.get(f"/missions/{mid}").text
    unknown = dg.post(f"/missions/{mid}/tache/{tid}/inconnue", follow_redirects=False)
    assert unknown.status_code == 404
    other = dg.post(f"/missions/{mid}/tache/9999/terminer", follow_redirects=False)
    assert other.status_code == 404
    refused = client("val@ibig.test").post("/autopilot", data={"instruction": "x" * 20})
    assert refused.status_code == 403
