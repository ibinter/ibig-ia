from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from ibig_agent.channels.mail import MailMessage
from ibig_agent.config import Mailbox, Settings
from ibig_agent.runtime import build_runtime

ROOT = Path(__file__).resolve().parents[1]

FAQ_SOFT = """---
titre: FAQ IBIG SOFT
pole: SOFT
type: faq
---
# FAQ IBIG SOFT

## Q: Quelle est la durée de l'essai gratuit ?
L'essai gratuit dure 14 jours, sans carte bancaire. Inscription sur https://ibigsoft.com.

## Q: Combien coûte l'abonnement de base ?
L'abonnement de base coûte 15 000 FCFA par mois. Contact : commercial@ibigsoft.com.
"""


class FakeLLM:
    """LLM scripté : renvoie le tri prévu pour chaque objet de mail."""

    def __init__(self):
        self.triages: dict[str, dict] = {}
        self.draft = "Bonjour,\n\nMerci pour votre intérêt pour nos solutions."
        self.calls: list[str] = []
        self.calendar: dict | None = None

    def structured(self, purpose, kind, system, user, schema, max_tokens=2000):
        self.calls.append(purpose)
        if purpose == "social.calendar":
            return self.calendar
        for subject, data in self.triages.items():
            if subject in user:
                base = {"pole": "SOFT", "categorie": "support", "urgence": "normale",
                        "sentiment": "neutre", "reclamation_grave": False,
                        "consigne_suspecte": False, "faq_id": "", "resume": "Résumé",
                        "prospect_nom": "", "prospect_besoin": ""}
                return {**base, **data}
        raise AssertionError(f"Aucun tri prévu pour : {user[:200]}")

    def write(self, purpose, system, user, max_tokens=4000):
        self.calls.append(purpose)
        return self.draft


class FakeConnector:
    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox
        self.inbox: list[MailMessage] = []
        self.sent: list[dict] = []
        self.labels: dict[str, list[str]] = {}
        self.fail_fetch = False

    def add(self, subject, body, sender="client@exemple.ci", mid=None):
        ref = str(len(self.inbox) + 1)
        self.inbox.append(MailMessage(
            mailbox=self.mailbox.adresse, message_id=mid or f"<{ref}@test>", ref=ref,
            sender=sender, sender_name="Client Test", subject=subject, body=body))

    def fetch_recent(self, days=3):
        if self.fail_fetch:
            raise ConnectionError("IMAP indisponible")
        return list(self.inbox)

    def send(self, to, subject, body, in_reply_to="", references="", thread_id=""):
        self.sent.append({"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to})
        return {"message_id": f"<sent{len(self.sent)}@test>"}

    def label(self, ref, labels):
        self.labels.setdefault(ref, []).extend(labels)


@pytest.fixture
def kb_dir(tmp_path):
    dest = tmp_path / "knowledge"
    shutil.copytree(ROOT / "knowledge", dest)
    (dest / "faq" / "soft.md").write_text(FAQ_SOFT, encoding="utf-8")
    return dest


@pytest.fixture
def config_dir(tmp_path):
    """Configuration du dépôt, avec valideur et suppléant renseignés pour SOFT."""
    dest = tmp_path / "config"
    shutil.copytree(ROOT / "config", dest)
    poles = yaml.safe_load((dest / "poles.yaml").read_text(encoding="utf-8"))
    for p in poles["poles"]:
        if p["code"] == "SOFT":
            p["valideur"], p["suppleant"] = "awa@ibig.test", "yao@ibig.test"
    (dest / "poles.yaml").write_text(yaml.safe_dump(poles, allow_unicode=True), encoding="utf-8")
    return dest


@pytest.fixture
def mailbox():
    return Mailbox(adresse="contact@ibigsoft.com", hebergeur="lws", pole="SOFT",
                   responsable="responsable.soft@ibigsoft.com",
                   signature="L'équipe IBIG SOFT")


@pytest.fixture
def connector(mailbox):
    return FakeConnector(mailbox)


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def rt(tmp_path, kb_dir, config_dir, llm, connector):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        config_dir=config_dir,
        knowledge_dir=kb_dir,
        secret_key="s" * 40,
        notification_mailbox=connector.mailbox.adresse,
        dashboard_url="https://tableau.ibig.test",
        _env_file=None,
    )
    return build_runtime(settings, llm=llm, connectors={connector.mailbox.adresse: connector})
