"""Les connexions IMAP/SMTP doivent vérifier le certificat du serveur (sinon, une
interception réseau suffit à voler les mots de passe des boîtes)."""

import ssl

import pytest

from ibig_agent.channels import mail as mail_mod
from ibig_agent.channels.mail import ImapSmtpConnector
from ibig_agent.config import Mailbox


class Stop(Exception):
    pass


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setenv("LWS_TEST_PWD", "secret")
    return ImapSmtpConnector(Mailbox(adresse="contact@ibigsoft.com", hebergeur="lws",
                                     pole="SOFT", imap_host="imap.test", smtp_host="smtp.test",
                                     password_env="LWS_TEST_PWD"))


def assert_verifying(ctx):
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_imap_verifies_certificate(box, monkeypatch):
    seen = {}

    def fake_imap(host, port, ssl_context=None, **kw):
        seen["ctx"] = ssl_context
        raise Stop

    monkeypatch.setattr(mail_mod.imaplib, "IMAP4_SSL", fake_imap)
    with pytest.raises(Stop):
        box.fetch_recent()
    assert_verifying(seen["ctx"])


def test_smtp_verifies_certificate(box, monkeypatch):
    seen = []

    def fake_smtp(host, port, context=None, **kw):
        seen.append(context)
        raise Stop

    monkeypatch.setattr(mail_mod.smtplib, "SMTP_SSL", fake_smtp)
    with pytest.raises(Stop):
        box.send("a@b.ci", "s", "b")
    assert len(seen) == 1
    assert_verifying(seen[0])
