from datetime import timedelta

import pytest

from ibig_agent.db import PendingAction, utcnow
from ibig_agent.governance import (
    ActionRequest,
    ChannelStopped,
    GovernanceError,
    Level,
    level_for,
)


def req(action_type, agent="messagerie", channel="mail", **kw):
    payload = kw.pop("payload", {"mailbox": "contact@ibigsoft.com", "to": "a@b.ci",
                                 "subject": "s", "body": "b"})
    return ActionRequest(agent=agent, action_type=action_type, channel=channel,
                         title=f"test {action_type}", payload=payload, **kw)


def test_unknown_action_defaults_to_strictest_level():
    assert level_for("action.inconnue") == Level.HUMAIN


def test_level1_executes_immediately(rt, connector):
    out = rt.governor.submit(req("mail.ack"))
    assert out.status == "executed"
    assert len(connector.sent) == 1


def test_level2_never_executes_without_approval(rt, connector):
    out = rt.governor.submit(req("mail.reply"))
    assert out.status == "queued"
    assert connector.sent == []  # R-04
    done = rt.governor.approve(out.pending_id, by="Awa")
    assert done.status == "executed"
    assert len(connector.sent) == 1


@pytest.mark.parametrize("action", ["refund", "discount", "contract", "legal", "payment",
                                    "media.reply", "crisis", "complaint.serious"])
def test_level3_is_never_executed_by_agent(rt, connector, action):
    rt.governor.executors[action] = lambda p: pytest.fail("ne doit jamais s'exécuter")
    out = rt.governor.submit(req(action))
    assert out.status == "prepared"
    with pytest.raises(GovernanceError):  # R-03
        rt.governor.approve(out.pending_id, by="Awa")
    rt.governor.mark_handled(out.pending_id, by="Direction")


def test_agent_can_escalate_but_not_downgrade(rt):
    assert req("mail.ack", escalate_to=Level.VALIDATION).level == Level.VALIDATION
    assert req("refund", escalate_to=Level.AUTOMATIQUE).level == Level.HUMAIN


def test_validation_only_agent_cannot_act_automatically(rt, connector):
    out = rt.governor.submit(req("mail.ack", agent="communication"))
    assert out.status == "queued"
    assert connector.sent == []


def test_chef_and_veille_cannot_act(rt):
    with pytest.raises(GovernanceError):
        rt.governor.submit(req("mail.ack", agent="chef"))
    with pytest.raises(GovernanceError):
        rt.governor.submit(req("mail.reply", agent="veille"))


def test_kill_switch_blocks_everything_immediately(rt, connector):
    queued = rt.governor.submit(req("mail.reply"))
    rt.governor.set_stopped("mail", True, by="Direction", reason="test")  # R-07
    assert rt.governor.submit(req("mail.ack")).status == "blocked"
    with pytest.raises(ChannelStopped):
        rt.governor.approve(queued.pending_id, by="Awa")
    assert connector.sent == []
    rt.governor.set_stopped("mail", False, by="Direction")
    assert rt.governor.submit(req("mail.ack")).status == "executed"


def test_global_kill_switch(rt, connector):
    rt.governor.set_stopped("*", True, by="Direction")
    assert rt.governor.submit(req("mail.ack")).status == "blocked"
    assert connector.sent == []


def test_approval_must_be_nominative(rt):
    out = rt.governor.submit(req("mail.reply"))
    with pytest.raises(GovernanceError):
        rt.governor.approve(out.pending_id, by="")


def test_stale_approvals_are_flagged_not_published(rt, connector):
    out = rt.governor.submit(req("mail.reply"))
    flagged = rt.governor.flag_stale(utcnow() + timedelta(hours=25))
    assert [f.id for f in flagged] == [out.pending_id]
    with rt.sessions() as s:
        assert s.get(PendingAction, out.pending_id).status == "pending"
    assert connector.sent == []


def test_executor_failure_is_journaled(rt):
    rt.governor.executors["mail.ack"] = lambda p: (_ for _ in ()).throw(RuntimeError("SMTP"))
    out = rt.governor.submit(req("mail.ack"))
    assert out.status == "failed" and "SMTP" in out.error


def test_placeholder_text_can_never_be_approved(rt, connector):
    out = rt.governor.submit(req("mail.reply", payload={
        "mailbox": connector.mailbox.adresse, "to": "a@b.ci", "subject": "s",
        "body": "Bonjour, le prix est [À COMPLÉTER : prix]."}))
    with pytest.raises(GovernanceError, match="incomplet"):
        rt.governor.approve(out.pending_id, by="Awa")
    assert connector.sent == []
    done = rt.governor.approve(out.pending_id, by="Awa",
                               payload_override={"body": "Bonjour, le prix est sur devis."})
    assert done.status == "executed" and connector.sent[0]["body"].endswith("sur devis.")


def test_a_stopped_department_blocks_only_its_own_actions(rt):
    from ibig_agent.governance import POLE_PREFIX, ActionRequest

    def report(pole):
        return rt.governor.submit(ActionRequest(agent="chef", action_type="report.publish",
                                                channel="rapport", title="Rapport", pole=pole,
                                                payload={"text": "bilan"}))

    assert report("SOFT").status == "executed"
    rt.governor.set_stopped(f"{POLE_PREFIX}SOFT", True, "Awa", "essai")
    blocked = report("SOFT")
    assert blocked.status == "blocked" and "SOFT" in blocked.error
    assert report("EDUFORM").status == "executed" and report("").status == "executed"
    assert rt.governor.is_pole_stopped("SOFT") and not rt.governor.is_pole_stopped("")
    rt.governor.set_stopped(f"{POLE_PREFIX}SOFT", False, "Awa")
    assert report("SOFT").status == "executed"


def test_stop_page_accepts_departments_and_refuses_unknown_ones(rt):
    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app

    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction",
                                          "mot-de-passe-solide")
    dg = TestClient(create_app(rt), base_url="https://testserver")
    dg.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    assert "Par département" in dg.get("/arret").text
    ok = dg.post("/arret", data={"canal": "pole:SOFT", "action": "stop", "motif": "essai"},
                 follow_redirects=False)
    assert ok.status_code == 303 and rt.governor.is_pole_stopped("SOFT")
    assert dg.post("/arret", data={"canal": "pole:NULLE", "action": "stop"},
                   follow_redirects=False).status_code == 400
    dg.post("/arret", data={"canal": "pole:SOFT", "action": "reprise"})
    assert not rt.governor.is_pole_stopped("SOFT")
