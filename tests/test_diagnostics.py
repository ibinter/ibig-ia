from types import SimpleNamespace

from ibig_agent.auth import UserStore
from ibig_agent.diagnostics import FAIL, OK, WARN, Diagnostic


def status(diag, area, name):
    return next(c for c in diag.checks if c.area == area and c.name == name)


class FakeModels:
    def __init__(self, known):
        self.known = known

    def retrieve(self, model):
        if model not in self.known:
            raise RuntimeError("not_found_error")
        return SimpleNamespace(id=model)


def test_offline_diagnostic(rt):
    diag = Diagnostic(rt, online=False)
    diag.run()
    assert status(diag, "Base", "version du schéma").status == OK
    assert status(diag, "Réglages", "clé de session").status == OK
    assert status(diag, "Réglages", "adresse du tableau de bord").status == OK  # https
    assert status(diag, "Gouvernance", "direction").status == FAIL
    assert status(diag, "Gouvernance", "valideur SOFT").status == FAIL  # pas de compte
    assert status(diag, "Gouvernance", "valideur EDUFORM").status == WARN
    assert not any(c.area in ("Mails", "IA", "Sites") for c in diag.checks)
    assert diag.failed and "en échec" in diag.as_text()


def test_accounts_fix_governance(rt):
    store = UserStore(rt.sessions, rt.org)
    store.create("dg@ibig.test", "DG", "direction", "mot-de-passe-1")
    store.create("awa@ibig.test", "Awa", "valideur", "mot-de-passe-1")
    diag = Diagnostic(rt, online=False)
    diag.run()
    assert not any(c.name == "direction" for c in diag.checks)
    assert status(diag, "Gouvernance", "valideur SOFT").status == OK


def test_online_checks(rt, connector):
    rt.llm.client = SimpleNamespace(models=FakeModels({rt.settings.triage_model}))
    connector.fail_fetch = False
    diag = Diagnostic(rt)
    diag.run()
    assert status(diag, "IA", "modèle de tri").status == OK
    assert status(diag, "IA", "modèle de rédaction").status == FAIL
    assert status(diag, "Mails", connector.mailbox.adresse).status == OK
    assert status(diag, "Sites", "IBIG SOFT").detail == "inactif (ignoré)"
    connector.fail_fetch = True
    diag = Diagnostic(rt)
    diag.run()
    assert "IMAP indisponible" in status(diag, "Mails", connector.mailbox.adresse).detail


def test_stopped_channel_and_missing_alert_mailbox(rt):
    rt.governor.set_stopped("mail", True, by="x")
    rt.settings.notification_mailbox = "inconnue@ibig.test"
    diag = Diagnostic(rt, online=False)
    diag.run()
    assert status(diag, "Gouvernance", "bouton d'arrêt").status == WARN
    assert status(diag, "Réglages", "boîte des alertes").status == FAIL
