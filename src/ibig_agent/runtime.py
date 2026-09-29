"""Assemblage de l'agent : configuration, base, gouvernance, connecteurs et agents."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from .agents.chef import ChefAgent
from .agents.communication import CommunicationAgent
from .agents.messagerie import MessagerieAgent
from .agents.notifications import ValidatorNotifier
from .channels.mail import MailConnector, connector_for
from .config import OrgConfig, Settings, get_settings, load_org_config
from .db import make_engine, open_db
from .governance import Executor, Governor
from .knowledge import KnowledgeBase
from .llm import LLM, ClaudeClient

log = logging.getLogger(__name__)


def mail_executors(connectors: dict[str, MailConnector]) -> dict[str, Executor]:
    def _connector(payload: dict) -> MailConnector:
        try:
            return connectors[payload["mailbox"]]
        except KeyError as exc:
            raise RuntimeError(f"Boîte non raccordée : {payload.get('mailbox')}") from exc

    def send(payload: dict) -> dict:
        return _connector(payload).send(
            to=payload["to"], subject=payload["subject"], body=payload["body"],
            in_reply_to=payload.get("in_reply_to", ""), references=payload.get("references", ""),
            thread_id=payload.get("thread_id", ""),
        )

    def forward(payload: dict) -> dict:
        return _connector(payload).send(to=payload["to"], subject=payload["subject"],
                                        body=payload["body"])

    def label(payload: dict) -> dict:
        _connector(payload).label(payload["ref"], payload["labels"])
        return {"labels": payload["labels"]}

    return {
        "mail.label": label,
        "mail.ack": send,
        "mail.faq_reply": send,
        "mail.reply": send,
        "mail.forward_internal": forward,
        "notify.internal": forward,
    }


def manual_social_executors() -> dict[str, Executor]:
    """Tant que l'outil multi-comptes (Metricool, Buffer…) n'est pas raccordé (phase 2),
    une publication validée est remise à l'équipe pour publication manuelle."""

    def manual(payload: dict) -> dict:
        return {"mode": "manuel", "a_publier_par": "équipe communication",
                "reseau": payload.get("reseau"), "compte": payload.get("compte")}

    return {"social.post": manual, "social.manual_post": manual,
            "report.publish": lambda payload: {"publie": "tableau de bord"}}


@dataclass
class Runtime:
    settings: Settings
    org: OrgConfig
    sessions: sessionmaker[Session]
    kb: KnowledgeBase
    governor: Governor
    llm: LLM | None
    connectors: dict[str, MailConnector] = field(default_factory=dict)

    @property
    def messagerie(self) -> MessagerieAgent:
        return MessagerieAgent(self.org, self.kb, self.llm, self.governor, self.sessions,
                               self.connectors)

    @property
    def communication(self) -> CommunicationAgent:
        return CommunicationAgent(self.org, self.kb, self.llm, self.governor)

    @property
    def notifier(self) -> ValidatorNotifier:
        return ValidatorNotifier(self.settings, self.org, self.governor, self.sessions,
                                 set(self.connectors))

    @property
    def chef(self) -> ChefAgent:
        llm = self.llm if isinstance(self.llm, ClaudeClient) else None
        return ChefAgent(self.governor, self.sessions, llm)


def build_runtime(settings: Settings | None = None, llm: LLM | None = None,
                  connectors: dict[str, MailConnector] | None = None,
                  with_llm: bool = True) -> Runtime:
    settings = settings or get_settings()
    org = load_org_config(settings.config_dir)
    sessions = open_db(make_engine(settings.database_url))
    kb = KnowledgeBase(settings.knowledge_dir)
    if connectors is None:
        connectors = {m.adresse: connector_for(m) for m in org.mailboxes}
    executors = {**mail_executors(connectors), **manual_social_executors()}
    governor = Governor(sessions, executors,
                        approval_timeout=timedelta(hours=settings.approval_timeout_hours))
    if llm is None and with_llm:
        llm = ClaudeClient(settings, sessions)
    return Runtime(settings, org, sessions, kb, governor, llm, connectors)
