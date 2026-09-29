from datetime import timedelta

from ibig_agent.auth import UserStore
from ibig_agent.db import utcnow
from ibig_agent.governance import ActionRequest


def queue(rt, connector, pole="SOFT", action="mail.reply", title="Brouillon"):
    return rt.governor.submit(ActionRequest(
        agent="messagerie", action_type=action, channel="mail", pole=pole, title=title,
        payload={"mailbox": connector.mailbox.adresse, "to": "client@x.ci", "subject": "S",
                 "body": "b"})).pending_id


def test_validator_gets_one_digest(rt, connector):
    queue(rt, connector, title="Réponse A")
    queue(rt, connector, title="Réponse B")
    result = rt.notifier.run()
    assert result.envoyes == 1 and result.elements == 2
    (mail,) = connector.sent
    assert mail["to"] == "awa@ibig.test"
    assert "Réponse A" in mail["body"] and "Réponse B" in mail["body"]
    assert "https://tableau.ibig.test/validations" in mail["body"]
    # Pas de doublon au passage suivant
    assert rt.notifier.run().envoyes == 0


def test_backup_is_alerted_after_24h(rt, connector):
    queue(rt, connector)
    rt.notifier.run()
    later = utcnow() + timedelta(hours=25)
    rt.governor.flag_stale(later)
    result = rt.notifier.run(later)
    assert result.envoyes == 1
    assert connector.sent[-1]["to"] == "yao@ibig.test"
    assert "24 h" in connector.sent[-1]["subject"]
    assert rt.notifier.run(later).envoyes == 0


def test_level3_goes_to_direction(rt, connector):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "DG", "direction", "mot-de-passe-1")
    queue(rt, connector, action="legal", title="Mise en demeure")
    rt.notifier.run()
    assert connector.sent[0]["to"] == "dg@ibig.test"
    assert "direction" in connector.sent[0]["subject"]


def test_pole_without_validator_falls_back_to_direction(rt, connector):
    queue(rt, connector, pole="EDUFORM")
    result = rt.notifier.run()
    assert result.sans_destinataire and connector.sent == []
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "DG", "direction", "mot-de-passe-1")
    rt.notifier.run()
    assert connector.sent[0]["to"] == "dg@ibig.test"


def test_disabled_without_notification_mailbox(rt, connector):
    rt.settings.notification_mailbox = ""
    queue(rt, connector)
    assert rt.notifier.run().desactive
    assert connector.sent == []


def test_failed_send_is_retried_later(rt, connector):
    queue(rt, connector)
    rt.governor.set_stopped("*", True, by="Direction")
    assert rt.notifier.run().envoyes == 0
    rt.governor.set_stopped("*", False, by="Direction")
    assert rt.notifier.run().envoyes == 1
