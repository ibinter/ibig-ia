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


def test_mission_report_gives_progress_cost_and_proofs(rt):
    ap = rt.autopilot
    mid = ap.from_procedure("traitement-ticket", "Ticket 7", "Awa", "SOFT")
    assert ap.report(mid)["closed"] is False and ap.report(mid)["done"] == 0
    for t in ap.tasks(mid):
        ap.start(t.id, "Awa")
        ap.complete(t.id, f"preuve {t.position}", "Awa", cost=0.01)
        t = ap.tasks(mid)
    rep = ap.report(mid)
    assert rep["closed"] and rep["done"] == rep["total"] == 3
    assert rep["cost_usd"] == pytest.approx(0.03) and rep["failed"] == 0
    assert [p[2] for p in rep["proofs"]] == ["preuve 1", "preuve 2", "preuve 3"]
    assert rep["duration_s"] >= 0


def test_each_priority_department_has_a_runnable_workflow(rt, llm):
    from ibig_agent import procedures

    assert len(procedures.PRIORITY_DEPARTMENTS) == 7
    for dept, code in procedures.PRIORITY_DEPARTMENTS:
        proc = procedures.get(code)
        assert proc is not None, dept
        assert proc.owner and proc.result and proc.quality and proc.exceptions, code
        assert any(s.proof for s in proc.steps), code
        mid = rt.autopilot.from_procedure(code, f"Test {dept}", "Awa", "SOFT")
        tasks = rt.autopilot.tasks(mid)
        assert len(tasks) == len(proc.steps) and tasks[0].status == "prete", code
    assert llm.calls == []                                   # aucun appel d'IA : zéro coût


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



@pytest.mark.parametrize("text,intent", [
    ("Combien de prospects avons-nous ce mois-ci ?", "question"),
    ("Quel est le prix de la formation Excel", "question"),
    ("J'autorise l'envoi du devis au collège", "decision"),
    ("Envoyer chaque lundi le point des ventes à la direction", "recurrent"),
    ("Rappeler le collège Palmiers demain", "ponctuelle"),
    ("Préparer le lancement de la formation Excel avancé", "mission"),
    ("Comment préparer le lancement de la formation Excel avancé ?", "question"),
])
def test_instruction_intent_is_detected(text, intent):
    from ibig_agent.autopilot import detect_intent
    assert detect_intent(text) == intent


def test_question_and_decision_create_no_mission_and_one_off_costs_nothing(rt, llm):
    ap = rt.autopilot
    assert ap.handle("Combien de prospects avons-nous ce mois-ci ?", "Awa") == ("question", None)
    assert ap.handle("J'autorise l'envoi du devis au collège", "Awa") == ("decision", None)
    intent, mid = ap.handle("Rappeler le collège Palmiers demain", "Awa", "SOFT")
    assert intent == "ponctuelle" and len(ap.tasks(mid)) == 1
    assert llm.calls == []                                   # aucun appel d'IA pour ces trois cas


def test_execute_prepares_ready_agent_tasks_but_never_closes_them(rt, llm):
    ap = rt.autopilot
    mid = ap.from_procedure("preparation-devis", "Collège Les Palmiers", "Awa", "SOFT")
    out = ap.execute(mid, "Awa")
    assert out["prepared"] == 1 and out["failed"] == 0
    first = ap.tasks(mid)[0]
    assert first.status == "en_cours" and first.result and first.evidence == ""
    assert ap.tasks(mid)[1].status == "a_faire"               # la clôture reste humaine


def test_execute_retries_then_fails_the_task_and_blocks_the_next(rt, llm, monkeypatch):
    from ibig_agent.llm import LLMError

    def boom(*a, **k):
        raise LLMError("modèle indisponible")

    monkeypatch.setattr(llm, "write", boom)
    ap = rt.autopilot
    mid = ap.from_procedure("preparation-devis", "Collège Les Palmiers", "Awa", "SOFT")
    out = ap.execute(mid, "Awa")
    first = ap.tasks(mid)[0]
    assert out["failed"] == 1 and first.status == "echec" and first.attempts == first.max_attempts
    assert ap.tasks(mid)[1].status == "bloquee"


def test_execute_stops_cleanly_when_credit_is_exhausted(rt, llm, monkeypatch):
    from ibig_agent.llm import LLMCreditExhausted

    def empty(*a, **k):
        raise LLMCreditExhausted("crédit épuisé")

    monkeypatch.setattr(llm, "write", empty)
    ap = rt.autopilot
    mid = ap.from_procedure("preparation-devis", "Collège Les Palmiers", "Awa", "SOFT")
    out = ap.execute(mid, "Awa")
    assert out["stopped"] and out["prepared"] == 0
    assert ap.tasks(mid)[0].status == "prete" and ap.tasks(mid)[0].attempts == 0


def test_every_department_points_to_real_agents_and_procedures():
    from ibig_agent import agent_control, departments, procedures

    assert len(departments.DEPARTMENTS) == 13
    assert len({d.code for d in departments.DEPARTMENTS}) == 13
    for d in departments.DEPARTMENTS:
        assert d.level in departments.LEVELS, d.code
        assert d.agents and all(a in agent_control.AGENTS for a in d.agents), d.code
        assert d.procedures and all(procedures.get(c) for c in d.procedures), d.code
        assert d.mission and d.kpis and d.limits and d.escalation, d.code


def test_departments_page_lists_every_department(rt):
    from fastapi.testclient import TestClient

    from ibig_agent import departments
    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction",
                                          "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    text = c.get("/departements").text
    assert all(d.name in text for d in departments.DEPARTMENTS)


def test_mission_budget_stops_the_executor(rt, llm):
    ap = rt.autopilot
    mid = ap.from_procedure("preparation-devis", "Collège Les Palmiers", "Awa", "SOFT")
    with pytest.raises(AutopilotError, match="négatif"):
        ap.set_budget(mid, -1, "Awa")
    ap.set_budget(mid, 0.01, "Awa")
    from ibig_agent.db import Mission
    with rt.sessions() as s:
        s.get(Mission, mid).spent_usd = 0.02                  # budget déjà dépassé
        s.commit()
    out = ap.execute(mid, "Awa")
    assert "Budget" in out["stopped"] and out["prepared"] == 0 and llm.calls == []
    ap.set_budget(mid, None, "Awa")                           # budget retiré : l'exécution reprend
    assert ap.execute(mid, "Awa")["prepared"] == 1


def _workflow_users(rt):
    from ibig_agent.auth import UserStore

    users = UserStore(rt.sessions, rt.org)
    users.create("a@ibig.test", "Awa", "direction", "mot-de-passe-solide")
    users.create("b@ibig.test", "Bakary", "direction", "mot-de-passe-solide")


def test_workflow_needs_another_approver_then_launches_a_mission(rt):
    from ibig_agent.workflows import WorkflowError

    _workflow_users(rt)
    wf = rt.workflows
    wid = wf.create("accueil-client", "Accueil d'un nouveau client", "Un client a payé", "SOFT",
                    [{"title": "Rédiger le message d'accueil", "agent": "communication",
                      "proof": "Brouillon relu"},
                     {"title": "Envoyer le message", "agent": "", "after": [1],
                      "proof": "Envoi confirmé"}], "Awa")
    with pytest.raises(WorkflowError, match="approuvé"):
        wf.launch(wid, "Collège Palmiers", "Awa")             # pas lançable avant approbation
    with pytest.raises(WorkflowError, match="autre personne"):
        wf.approve(wid, "Awa")                                # l'auteur n'approuve pas
    wf.approve(wid, "Bakary")
    mid = wf.launch(wid, "Collège Palmiers", "Awa")
    tasks = rt.autopilot.tasks(mid)
    assert [t.status for t in tasks] == ["prete", "a_faire"] and tasks[0].agent == "communication"
    v2 = wf.create("accueil-client", "Accueil d'un nouveau client", "", "SOFT",
                   [{"title": "Une seule étape", "agent": ""}], "Awa")
    assert wf.get(v2).version == 2
    wf.approve(v2, "Bakary")
    assert wf.get(wid).status == "archive" and wf.get(v2).status == "approuve"


def test_workflow_steps_are_validated_and_library_can_be_adopted(rt):
    from ibig_agent.workflows import WorkflowError, parse_text

    wf = rt.workflows
    with pytest.raises(WorkflowError, match="inexistante"):
        wf.create("x", "X", "", "", [{"title": "A", "after": [2]}, {"title": "B"}], "Awa")
    with pytest.raises(WorkflowError, match="agent inconnu"):
        wf.create("x", "X", "", "", [{"title": "A", "agent": "robot"}], "Awa")
    assert parse_text("Écrire | communication | | Brouillon\nEnvoyer | | 1 | Envoi")[1]["after"] == [1]
    wid = wf.adopt("traitement-ticket", "Awa")
    assert len(wf.get(wid).steps) == 3 and wf.get(wid).status == "brouillon"


def test_workflows_page_flow_and_access(rt):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    users = UserStore(rt.sessions, rt.org)
    for email, role in (("a@ibig.test", "direction"), ("b@ibig.test", "direction"),
                        ("v@ibig.test", "valideur")):
        users.create(email, email[0], role, "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    a, b = client("a@ibig.test"), client("b@ibig.test")
    a.post("/workflows", data={"code": "mon-flux", "titre": "Mon flux", "pole": "SOFT",
                               "etapes": "Préparer | communication | | Brouillon\nEnvoyer | | 1 | Envoi"})
    assert "Mon flux" in a.get("/workflows").text
    wid = rt.workflows.all()[0].id
    refused = a.post(f"/workflows/{wid}/approuver", follow_redirects=False)
    assert "Refus" in refused.headers["location"] or "Refus" in a.get("/workflows").text
    b.post(f"/workflows/{wid}/approuver")
    made = b.post(f"/workflows/{wid}/lancer", data={"sujet": "Client X", "pole": "SOFT"},
                  follow_redirects=False)
    assert "/missions/" in made.headers["location"]
    assert client("v@ibig.test").get("/workflows").status_code == 403
