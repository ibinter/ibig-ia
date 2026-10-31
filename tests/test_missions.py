"""Lot 2 — suspension et quotas des agents, missions : cycle de vie, limites, pauses."""

from urllib.parse import unquote

import pytest
from sqlalchemy import select

from ibig_agent import agent_control
from ibig_agent.db import AIUsage, JournalEntry, MissionEvent
from ibig_agent.governance import ActionRequest, GovernanceError
from ibig_agent.missions import MissionError, MissionService, RunResult


def report(rt, agent="chef"):
    return rt.governor.submit(ActionRequest(agent=agent, action_type="report.publish",
                                            channel="rapport", title="Rapport",
                                            payload={"text": "bilan"}))


# ------------------------------------------------------------------ suspension et quotas
def test_a_suspended_agent_cannot_act_and_is_back_after_resume(rt):
    assert report(rt).status == "executed"
    agent_control.set_suspended(rt.sessions, "chef", True, "Awa", "essai de suspension")
    out = report(rt)
    assert out.status == "blocked" and "suspendu" in out.error and "essai" in out.error
    with rt.sessions() as s:
        blocked = s.scalars(select(JournalEntry).where(JournalEntry.status == "blocked")).all()
        stop = s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "agent.suspendu")).one()
    assert blocked and stop.decided_by == "Awa"
    agent_control.set_suspended(rt.sessions, "chef", False, "Awa")
    assert report(rt).status == "executed"


def test_other_agents_are_not_affected_by_a_suspension(rt):
    agent_control.set_suspended(rt.sessions, "commercial", True, "Awa")
    assert report(rt, "chef").status == "executed"
    with pytest.raises(ValueError, match="inconnu"):
        agent_control.set_suspended(rt.sessions, "pirate", True, "Awa")


def test_daily_action_quota_blocks_until_tomorrow(rt):
    agent_control.set_quotas(rt.sessions, "chef", 2, None, "Awa")
    assert [report(rt).status for _ in range(3)] == ["executed", "executed", "blocked"]
    assert "Quota quotidien atteint" in report(rt).error
    assert agent_control.usage_today(rt.sessions, "chef").runs == 2
    agent_control.set_quotas(rt.sessions, "chef", None, None, "Awa")      # plus de limite
    assert report(rt).status == "executed"


def test_daily_cost_cap_counts_the_ai_usage_of_that_agent_only(rt):
    with rt.sessions() as s:
        s.add(AIUsage(model="m", purpose="commercial.qualify", cost_usd=3.0))
        s.add(AIUsage(model="m", purpose="chef.weekly_plan", cost_usd=0.4))
        s.add(AIUsage(model="m", purpose="kb.index", cost_usd=9.0))   # sans agent : ignoré
        s.commit()
    assert agent_control.usage_today(rt.sessions, "commercial").cost == 3.0
    assert agent_control.usage_today(rt.sessions, "chef").cost == 0.4
    agent_control.set_quotas(rt.sessions, "commercial", None, 2.5, "Awa")
    assert "Plafond de coût" in agent_control.blocked_reason(rt.sessions, "commercial")
    assert agent_control.blocked_reason(rt.sessions, "chef") == ""
    with pytest.raises(ValueError, match="positif"):
        agent_control.set_quotas(rt.sessions, "chef", 0, None, "Awa")


def test_a_validation_cannot_bypass_a_suspension(rt):
    out = rt.governor.submit(ActionRequest(agent="communication", action_type="social.post",
                                           channel="facebook", title="Post", account="IBIG",
                                           payload={"texte": "Bonjour", "reseau": "facebook_page",
                                                    "compte": "IBIG", "date": "2099-01-01"}))
    assert out.status == "queued"
    agent_control.set_suspended(rt.sessions, "communication", True, "Awa")
    with pytest.raises(GovernanceError, match="suspendu"):
        rt.governor.approve(out.pending_id, by="Awa")
    agent_control.set_suspended(rt.sessions, "communication", False, "Awa")
    assert rt.governor.approve(out.pending_id, by="Awa").status in ("executed", "failed")


# ------------------------------------------------------------------ missions
DATA = {"name": "Écoles privées — SCOLABY", "pole": "SOFT", "product": "SCOLABY ERP",
        "zone": "Abidjan", "target": "Établissements scolaires privés", "budget_usd": "10",
        "max_per_day": "20", "max_total": "100", "objective": "Jusqu'à 100 prospects"}


@pytest.fixture
def svc(rt):
    return MissionService(rt.sessions, rt.governor, rt.org.pole_codes, runners={})


def make(svc, **over):
    return svc.create({**DATA, **over}, "Awa")


def approved(svc, **over):
    mid = make(svc, **over)
    svc.simulate(mid, "Awa")
    svc.approve(mid, "Awa")
    return mid


def events(rt, mid):
    with rt.sessions() as s:
        return [e.summary for e in s.scalars(select(MissionEvent).where(
            MissionEvent.mission_id == mid).order_by(MissionEvent.id)).all()]


def test_mission_input_is_validated(svc):
    for bad, msg in (({"name": ""}, "obligatoire"), ({"pole": "NULLE"}, "Pôle inconnu"),
                     ({"channel": "pigeon"}, "Canal inconnu"), ({"agent": "pirate"}, "Agent"),
                     ({"budget_usd": "-5"}, "positif"), ({"max_total": "abc"}, "nombre"),
                     ({"autonomy": "contact_libre"}, "Autonomie")):
        with pytest.raises(MissionError, match=msg):
            make(svc, **bad)
    assert svc.get(make(svc)).status == "brouillon"


def test_full_lifecycle_needs_simulation_then_approval_then_launch(rt, svc):
    mid = make(svc)
    with pytest.raises(MissionError, match="simulation"):
        svc.approve(mid, "Awa")                                   # pas de saut d'étape
    with pytest.raises(MissionError, match="approuvée"):
        svc.launch(mid, "Awa")
    checks = svc.simulate(mid, "Awa")
    assert svc.get(mid).status == "simule" and all(c.ok for c in checks if c.blocking)
    assert any("aucune recherche ni contact" in c.detail for c in checks)   # honnêteté : pas d'agent
    svc.approve(mid, "Direction")
    assert svc.get(mid).approved_by == "Direction"
    svc.launch(mid, "Direction")
    svc.pause(mid, "Awa", "vérification")
    assert svc.get(mid).status == "en_pause" and svc.get(mid).pause_reason == "vérification"
    svc.resume(mid, "Awa")
    svc.stop(mid, "Awa")
    assert svc.get(mid).status == "arrete" and svc.get(mid).ended_at is not None
    with pytest.raises(MissionError, match="déjà"):
        svc.stop(mid, "Awa")
    log = events(rt, mid)
    assert log[0] == "Brouillon créé" and "Lancée" in log and log[-1] == "Arrêtée"


def test_a_mission_without_any_cap_cannot_pass_simulation(rt, svc):
    mid = make(svc, budget_usd="", max_total="")
    checks = svc.simulate(mid, "Awa")
    assert svc.get(mid).status == "brouillon"
    assert any(c.label == "Plafond de la mission" and not c.ok for c in checks)
    assert "Plafond de la mission" in events(rt, mid)[-1]


def test_editing_a_simulated_mission_requires_a_new_simulation(svc):
    mid = make(svc)
    svc.simulate(mid, "Awa")
    svc.update(mid, {**DATA, "zone": "Bouaké"}, "Awa")
    m = svc.get(mid)
    assert (m.status, m.zone, m.simulated_at) == ("brouillon", "Bouaké", None)
    with pytest.raises(MissionError, match="simulation"):
        svc.approve(mid, "Awa")
    svc.simulate(mid, "Awa")
    svc.approve(mid, "Awa")
    with pytest.raises(MissionError, match="brouillon ou simulée"):
        svc.update(mid, DATA, "Awa")


def test_launch_and_resume_recheck_the_agent_and_the_emergency_stop(rt, svc):
    mid = approved(svc)
    agent_control.set_suspended(rt.sessions, "prospecteur", True, "Awa", "revue")
    with pytest.raises(MissionError, match="suspendu"):
        svc.launch(mid, "Awa")
    agent_control.set_suspended(rt.sessions, "prospecteur", False, "Awa")
    svc.launch(mid, "Awa")
    rt.governor.set_stopped("*", True, "Awa", "urgence")
    stats = svc.tick()                                            # la surveillance la met en pause
    assert stats["mises_en_pause"] == 1
    m = svc.get(mid)
    assert m.status == "en_pause" and "arrêt d'urgence" in m.pause_reason
    with pytest.raises(MissionError, match="Arrêt d'urgence"):
        svc.resume(mid, "Awa")
    rt.governor.set_stopped("*", False, "Awa")
    svc.resume(mid, "Awa")
    assert svc.get(mid).status == "en_cours"


def test_tick_pauses_when_the_agent_gets_suspended(rt, svc):
    mid = approved(svc)
    svc.launch(mid, "Awa")
    agent_control.set_suspended(rt.sessions, "prospecteur", True, "Awa")
    assert svc.tick()["mises_en_pause"] == 1
    assert "agent suspendu" in svc.get(mid).pause_reason


def test_without_an_executing_agent_the_mission_waits_and_says_so_once(rt, svc):
    mid = approved(svc)
    svc.launch(mid, "Awa")
    assert svc.tick()["en_attente"] == 1 and svc.tick()["en_attente"] == 1
    waiting = [e for e in events(rt, mid) if e.startswith("En attente d'un agent")]
    assert len(waiting) == 1                                      # pas de bruit à chaque passage
    assert svc.get(mid).runs == 0 and svc.get(mid).status == "en_cours"


def test_runner_results_budget_alert_then_automatic_pause(rt):
    calls = []

    def runner(m):
        calls.append(m.id)
        return RunResult(True, "12 prospects vérifiés", cost=4.0)

    svc = MissionService(rt.sessions, rt.governor, rt.org.pole_codes,
                         runners={"prospection": runner})
    mid = approved(svc, budget_usd="10", max_per_day="", max_total="")
    svc.launch(mid, "Awa")
    svc.tick()                                                    # 4 / 10
    assert svc.get(mid).status == "en_cours"
    svc.tick()                                                    # 8 / 10 : alerte à 80 %
    assert any(e.startswith("Budget à 80 %") for e in events(rt, mid))
    assert svc.get(mid).status == "en_cours"
    svc.tick()                                                    # 12 / 10 : pause automatique
    m = svc.get(mid)
    assert m.status == "en_pause" and m.pause_reason == "budget atteint" and len(calls) == 3
    with rt.sessions() as s:
        alerts = s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "mission.alerte")).all()
    assert len(alerts) == 2 and all(a.status == "flagged" for a in alerts)
    assert svc.tick()["surveillees"] == 0                         # en pause : plus exécutée
    with pytest.raises(MissionError, match="Budget"):
        svc.resume(mid, "Awa")                                    # budget épuisé : pas de reprise


def test_repeated_failures_pause_the_mission_and_a_success_resets_the_count(rt):
    outcomes = iter([False, True, False, False, False])

    def runner(m):
        return RunResult(next(outcomes), "résultat")

    svc = MissionService(rt.sessions, rt.governor, rt.org.pole_codes,
                         runners={"prospection": runner})
    mid = approved(svc, max_per_day="", budget_usd="", max_total="50")
    svc.launch(mid, "Awa")
    for _ in range(4):
        svc.tick()
    assert svc.get(mid).status == "en_cours" and svc.get(mid).consecutive_failures == 2
    svc.tick()
    m = svc.get(mid)
    assert m.status == "en_pause" and "3 échecs de suite" in m.pause_reason and m.failures == 4


def test_total_volume_completes_the_mission_and_the_agent_error_is_counted(rt):
    def boom(m):
        raise RuntimeError("source injoignable")

    svc = MissionService(rt.sessions, rt.governor, rt.org.pole_codes,
                         runners={"prospection": boom})
    mid = approved(svc, max_per_day="", budget_usd="", max_total="2", error_threshold="5")
    svc.launch(mid, "Awa")
    svc.tick()
    assert svc.get(mid).failures == 1 and "source injoignable" in svc.get(mid).last_result
    svc.tick()
    assert svc.get(mid).status == "termine" and svc.get(mid).ended_at is not None


def test_daily_volume_limit_spreads_the_work(rt):
    svc = MissionService(rt.sessions, rt.governor, rt.org.pole_codes,
                         runners={"prospection": lambda m: RunResult(True, "ok")})
    mid = approved(svc, max_per_day="2", budget_usd="", max_total="50")
    svc.launch(mid, "Awa")
    for _ in range(5):
        svc.tick()
    assert svc.get(mid).runs == 2                                  # 2 par jour, pas davantage


def test_missions_are_listed_newest_first_and_the_job_is_scheduled(rt, svc):
    from ibig_agent.scheduler import job_specs

    a, b = make(svc, name="A"), make(svc, name="B")
    assert [m.id for m in svc.all_missions()] == [b, a]
    assert [m.id for m in svc.all_missions("simule")] == []
    assert "missions_tick" in {j.id for j in job_specs(rt)}


# ------------------------------------------------------------------ pages du tableau de bord
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


FORM = {"name": "Écoles privées", "kind": "prospection", "pole": "SOFT", "product": "SCOLABY",
        "zone": "Abidjan", "target": "Écoles", "objective": "100 prospects", "criteria": "",
        "agent": "prospecteur", "channel": "", "autonomy": "validation", "budget_usd": "10",
        "max_per_day": "20", "max_total": "100", "error_threshold": "3"}


def test_missions_pages_run_the_whole_cycle_and_warn_that_no_agent_executes_yet(rt, web):
    dg = web("dg@ibig.test")
    page = dg.get("/missions").text
    assert "ne contacte personne" in page and "Nouvelle mission" in page
    created = dg.post("/missions", data=FORM, follow_redirects=False)
    mid = int(created.headers["location"].split("/missions/")[1].split("?")[0])
    detail = dg.get(f"/missions/{mid}").text
    assert "Écoles privées" in detail and "Brouillon" in detail and "Simuler" in detail
    assert "Déposer des prospects" in detail
    sent = dg.post(f"/missions/{mid}/fichier", follow_redirects=False,
                   files={"fichier": ("p.csv", "email\nboss@societe.ci\n", "text/csv")})
    assert "reconnue" in unquote(sent.headers["location"])
    empty = dg.post(f"/missions/{mid}/fichier", follow_redirects=False,
                    files={"fichier": ("p.csv", "nom\nAwa\n", "text/csv")})
    assert "Refus" in empty.headers["location"]
    dg.post(f"/missions/{mid}/simuler")
    assert "Simulée" in dg.get(f"/missions/{mid}").text
    dg.post(f"/missions/{mid}/approuver")
    dg.post(f"/missions/{mid}/lancer")
    detail = dg.get(f"/missions/{mid}").text
    assert "En cours" in detail and "Mettre en pause" in detail
    dg.post(f"/missions/{mid}/pause", data={"motif": "revue"})
    assert "pause : revue" in dg.get(f"/missions/{mid}").text
    dg.post(f"/missions/{mid}/arreter")
    assert "Arrêtée" in dg.get(f"/missions/{mid}").text
    assert "Écoles privées" in dg.get("/missions", params={"statut": "arrete"}).text
    refused = dg.post("/missions", data={**FORM, "pole": "NULLE"}, follow_redirects=False)
    assert "Refus" in refused.headers["location"]
    skipped = dg.post(f"/missions/{mid}/lancer", follow_redirects=False)
    assert "Refus" in skipped.headers["location"]                   # étape impossible : refusée
    assert dg.post(f"/missions/{mid}/inconnue", follow_redirects=False).status_code == 404


def test_supervision_page_suspends_resumes_and_sets_quotas(rt, web):
    dg = web("dg@ibig.test")
    page = dg.get("/supervision").text
    assert "Supervision des agents" in page and "Commercial" in page and "Prospecteur" in page
    dg.post("/supervision/chef/suspendre", data={"motif": "essai"})
    assert "suspendu" in dg.get("/supervision").text
    assert report(rt).status == "blocked"                           # l'agent est bien arrêté
    dg.post("/supervision/chef/reprendre")
    assert report(rt).status == "executed"
    dg.post("/supervision/chef/quotas", data={"actions": "2", "cout": "2,5"})
    ctl = agent_control.control(rt.sessions, "chef")
    assert (ctl.runs_cap_day, ctl.cost_cap_day) == (2, 2.5)
    # une action a déjà été exécutée aujourd'hui (après la reprise) : une seule de plus passe
    assert report(rt).status == "executed" and report(rt).status == "blocked"
    refused = dg.post("/supervision/chef/quotas", data={"actions": "abc"}, follow_redirects=False)
    assert "Refus" in refused.headers["location"]
    assert "Refus" in dg.post("/supervision/pirate/suspendre",
                              follow_redirects=False).headers["location"]


def test_a_validator_cannot_open_or_change_missions_or_agents(rt, web):
    val = web("val@ibig.test")
    for path in ("/missions", "/supervision"):
        assert val.get(path).status_code == 403
    assert val.post("/missions", data=FORM, follow_redirects=False).status_code == 403
    assert val.post("/supervision/chef/suspendre", follow_redirects=False).status_code == 403
    assert agent_control.control(rt.sessions, "chef") is None


def test_every_agent_has_a_job_sheet_consistent_with_governance():
    from ibig_agent import agent_control, agent_profiles
    from ibig_agent.governance import ACTION_LEVELS, AGENT_AUTONOMY, Level

    assert set(agent_profiles.PROFILES) == set(agent_control.AGENTS)
    for agent, prof in agent_profiles.PROFILES.items():
        assert prof.mission and prof.skills and prof.forbidden and prof.escalation
        for action in prof.actions:
            assert action in ACTION_LEVELS, (agent, action)
        if AGENT_AUTONOMY.get(agent, "aucune") in ("aucune", "lecture"):
            # un agent « lecture » ne soumet que des actions internes, jamais un envoi
            assert all(ACTION_LEVELS[a] != Level.VALIDATION for a in prof.actions), agent
    assert agent_profiles.autonomy_of("messagerie") == ("automatique", "vert")
    assert agent_profiles.autonomy_of("prospecteur")[1] == "rouge"


def test_supervision_shows_job_sheets_and_saves_the_human_owner(rt, web):
    dg = web("dg@ibig.test")
    page = dg.get("/supervision").text
    assert "Fiche de poste" in page and "Responsable humain" in page
    done = dg.post("/supervision/commercial/responsable", data={"responsable": "Koné Awa"},
                   follow_redirects=False)
    assert "Refus" not in done.headers["location"]
    assert "Koné Awa" in dg.get("/supervision").text
    assert dg.post("/supervision/pirate/responsable", data={"responsable": "x"},
                   follow_redirects=False).status_code == 404
    assert web("val@ibig.test").post("/supervision/commercial/responsable",
                                     data={"responsable": "x"}).status_code == 403
