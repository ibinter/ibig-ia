from fastapi.testclient import TestClient

from ibig_agent.dashboard.app import create_app
from ibig_agent.governance import ActionRequest


def client(rt, login=True):
    c = TestClient(create_app(rt))
    if login:
        r = c.post("/login", data={"nom": "Awa", "token": "secret-test"}, follow_redirects=False)
        assert r.status_code == 303
    return c


def test_requires_login(rt):
    c = client(rt, login=False)
    r = c.get("/validations", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = c.post("/login", data={"nom": "X", "token": "faux"}, follow_redirects=False)
    assert "invalides" in r.headers["location"]


def test_one_click_approval_with_edit(rt, connector):
    out = rt.governor.submit(ActionRequest(
        agent="messagerie", action_type="mail.reply", channel="mail", title="Réponse test",
        payload={"mailbox": connector.mailbox.adresse, "to": "a@b.ci", "subject": "S",
                 "body": "Brouillon"}))
    c = client(rt)
    assert "Réponse test" in c.get("/validations").text
    c.post(f"/validations/{out.pending_id}/approuver", data={"contenu": "Texte corrigé"})
    assert connector.sent[0]["body"] == "Texte corrigé"
    assert "Awa" in c.get("/journal").text


def test_kill_switch_from_dashboard(rt):
    c = client(rt)
    c.post("/arret", data={"canal": "*", "action": "stop", "motif": "incident"})
    assert rt.governor.is_stopped("mail")
    c.post("/arret", data={"canal": "*", "action": "reprise"})
    assert not rt.governor.is_stopped("mail")


def test_home_page(rt):
    assert "Synthèse" in client(rt).get("/").text
