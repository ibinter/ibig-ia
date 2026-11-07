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


def _link(body):
    import re
    return re.search(r"https://tableau\.ibig\.test(/v/\S+)", body).group(1)


def test_one_click_link_validates_after_confirmation(rt, connector):
    from fastapi.testclient import TestClient

    from ibig_agent.dashboard.app import create_app
    from ibig_agent.db import PendingAction

    UserStore(rt.sessions, rt.org).create("awa@ibig.test", "Awa", "valideur", "mot-de-passe-1")
    pid = queue(rt, connector, title="Réponse au devis")
    rt.notifier.run()
    path = _link(connector.sent[0]["body"])
    c = TestClient(create_app(rt), base_url="https://testserver")
    page = c.get(path).text  # un simple clic (ou un antivirus qui ouvre le lien) n'exécute rien
    assert "Réponse au devis" in page and "Valider et envoyer" in page
    with rt.sessions() as s:
        assert s.get(PendingAction, pid).status == "pending"
    assert "Validé et exécuté" in c.post(path, data={"decision": "valider"}).text
    with rt.sessions() as s:
        pa = s.get(PendingAction, pid)
    assert pa.status == "executed" and pa.decided_by == "Awa <awa@ibig.test> (lien mail)"
    assert "déjà traitée" in c.post(path, data={"decision": "valider"}).text


def test_link_is_refused_when_forged_or_expired(rt, connector):
    import time

    from fastapi.testclient import TestClient

    from ibig_agent.auth import make_action_token
    from ibig_agent.dashboard.app import create_app

    store = UserStore(rt.sessions, rt.org)
    awa = store.create("awa@ibig.test", "Awa", "valideur", "mot-de-passe-1")
    pid = queue(rt, connector)
    c = TestClient(create_app(rt), base_url="https://testserver")
    old = make_action_token(rt.settings.secret_key, pid, awa.id, now=time.time() - 49 * 3600)
    assert "expiré" in c.get(f"/v/{old}").text
    forged = make_action_token("x" * 40, pid, awa.id)
    assert "expiré" in c.get(f"/v/{forged}").text
    other = queue(rt, connector, pole="EDUFORM")  # pôle qu'Awa ne valide pas
    token = make_action_token(rt.settings.secret_key, other, awa.id)
    assert "ne vous permet pas" in c.post(f"/v/{token}", data={"decision": "valider"}).text


def test_no_link_for_level3_or_without_account(rt, connector):
    queue(rt, connector)  # awa n'a pas de compte : pas de lien
    rt.notifier.run()
    assert "/v/" not in connector.sent[0]["body"]
