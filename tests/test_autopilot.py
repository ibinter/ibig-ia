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


def test_agent_prepares_a_draft_but_a_human_closes_the_task(ap, rt, llm):
    mid = ap.plan(INSTRUCTION, "Awa")
    first, second = ap.tasks(mid)[0], ap.tasks(mid)[1]
    with pytest.raises(AutopilotError, match="prête ou en cours"):
        ap.prepare(second.id, "Awa")                       # dépend d'une tâche non terminée
    draft = ap.prepare(first.id, "Awa")
    assert draft == llm.draft and "chef.autopilot_task" in llm.calls
    task = ap.tasks(mid)[0]
    assert task.status == "en_cours" and task.result == llm.draft and task.attempts == 1
    ap.complete(first.id, "relu et validé par Awa", "Awa")
    assert ap.tasks(mid)[1].status == "prete"
    with pytest.raises(AutopilotError, match="personne"):
        ap.prepare(ap.tasks(mid)[1].id, "Awa")             # la tâche 2 est confiée à un humain


def test_a_suspended_agent_cannot_prepare_drafts(ap, rt):
    from ibig_agent import agent_control

    mid = ap.plan(INSTRUCTION, "Awa")
    agent_control.set_suspended(rt.sessions, "communication", True, "Awa", "essai")
    with pytest.raises(AutopilotError, match="suspendu"):
        ap.prepare(ap.tasks(mid)[0].id, "Awa")


def test_prepare_button_stores_the_draft_on_the_page(rt, llm):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    llm.autopilot_plan = PLAN
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction",
                                          "mot-de-passe-solide")
    dg = TestClient(create_app(rt), base_url="https://testserver")
    dg.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    made = dg.post("/autopilot", data={"instruction": INSTRUCTION, "pole": "SOFT"},
                   follow_redirects=False)
    mid = int(made.headers["location"].split("/missions/")[1].split("?")[0])
    tid = rt.autopilot.tasks(mid)[0].id
    assert "Faire préparer un brouillon" in dg.get(f"/missions/{mid}").text
    dg.post(f"/missions/{mid}/tache/{tid}/preparer")
    assert llm.draft[:20] in dg.get(f"/missions/{mid}").text


def test_every_procedure_is_a_valid_acyclic_plan():
    from ibig_agent import agent_control, procedures

    assert len(procedures.PROCEDURES) >= 10
    assert len({p.code for p in procedures.PROCEDURES}) == len(procedures.PROCEDURES)
    for proc in procedures.PROCEDURES:
        for n, step in enumerate(proc.steps, 1):
            assert all(1 <= a < n for a in step.after), (proc.code, n)
            assert step.agent == "" or step.agent in agent_control.AGENTS, (proc.code, n)
        for step in proc.steps:                              # un agent n'envoie, ne publie, n'active rien
            if step.title.split()[0] in ("Envoyer", "Publier", "Activer", "Informer", "Répondre", "Remettre"):
                assert step.agent == "", (proc.code, step.title)


def test_a_procedure_creates_a_mission_without_any_ai_call(rt, llm):
    mid = rt.autopilot.from_procedure("preparation-devis", "Collège Les Palmiers", "Awa", "SOFT")
    assert llm.calls == []                                   # zéro appel d'IA : zéro coût
    tasks = rt.autopilot.tasks(mid)
    assert [t.status for t in tasks] == ["prete", "a_faire", "a_faire", "a_faire"]
    assert tasks[0].agent == "commercial" and "Preuve attendue" in tasks[0].detail
    with pytest.raises(AutopilotError, match="inconnue"):
        rt.autopilot.from_procedure("nulle", "x", "Awa")
    with pytest.raises(AutopilotError, match="sujet"):
        rt.autopilot.from_procedure("preparation-devis", " ", "Awa")


def test_procedures_page_launches_a_procedure(rt):
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

    dg = client("dg@ibig.test")
    assert "Préparation d" in dg.get("/procedures").text
    made = dg.post("/procedures/traitement-ticket/lancer", data={"sujet": "Ticket 42",
                                                                 "pole": "SOFT"},
                   follow_redirects=False)
    mid = int(made.headers["location"].split("/missions/")[1].split("?")[0])
    assert "Ticket 42" in dg.get(f"/missions/{mid}").text
    assert dg.post("/procedures/nulle/lancer", data={"sujet": "x"},
                   follow_redirects=False).headers["location"].count("Refus") == 1
    assert client("val@ibig.test").get("/procedures").status_code == 403

