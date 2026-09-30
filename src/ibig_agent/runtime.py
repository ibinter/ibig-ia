"""Assemblage de l'agent : configuration, base, gouvernance, connecteurs et agents."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from .agents.campagnes import CampaignWriter
from .agents.chef import ChefAgent
from .agents.commercial import CommercialAgent
from .agents.communication import CommunicationAgent
from .agents.contenus_web import ContenusWebAgent
from .agents.messagerie import MessagerieAgent
from .agents.notifications import ValidatorNotifier
from .agents.plan import WeeklyPlanner
from .agents.revue import MonthlyReviewer
from .agents.support import SupportAgent
from .agents.veille import VeilleAgent
from .agents.whatsapp import WhatsAppAgent
from .channels.mail import MailConnector, connector_for
from .channels.web import WebConnector, sanitize_html, web_connector_for
from .channels.whatsapp import WINDOW_HOURS, WhatsAppClient
from .config import OrgConfig, Settings, get_settings, load_org_config
from .configstore import directives_text, knowledge_overrides, sync_mailboxes
from .db import WhatsAppContact, make_engine, open_db, utcnow
from .governance import Executor, Governor, as_utc
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
        "commercial.followup": send,
        "support.answer": send,
        "mail.forward_internal": forward,
        "notify.internal": forward,
    }


def web_executors(connectors: dict[str, WebConnector]) -> dict[str, Executor]:
    def draft(payload: dict) -> dict:
        connector = connectors.get(payload["site"])
        if connector is None:
            raise RuntimeError(f"Site non raccordé : {payload['site']}")
        # Nettoyé à nouveau : le valideur a pu modifier le HTML avant de valider.
        return connector.create_draft({**payload,
                                       "contenu_html": sanitize_html(payload["contenu_html"])})

    return {"web.article_draft": draft}


def whatsapp_executors(clients: dict[str, WhatsAppClient],
                       sessions: sessionmaker[Session]) -> dict[str, Executor]:
    def send(payload: dict) -> dict:
        client = clients.get(payload["phone_number_id"])
        if client is None:
            raise RuntimeError(f"Numéro WhatsApp non raccordé : {payload['phone_number_id']}")
        with sessions() as s:
            contact = s.get(WhatsAppContact, payload["to"])
        if contact is not None and contact.opted_out:
            raise RuntimeError("contact désinscrit (STOP) : aucun envoi")
        # Règle de Meta, vérifiée au moment de l'envoi : la validation a pu prendre du temps.
        if (contact is None or contact.last_inbound_at is None or utcnow()
                - as_utc(contact.last_inbound_at) > timedelta(hours=WINDOW_HOURS)):
            raise RuntimeError("fenêtre de 24 h dépassée : répondre avec un modèle validé "
                               "par Meta ou par téléphone")
        return client.send_text(payload["to"], payload["texte"])

    return {"whatsapp.reply": send, "whatsapp.ack": send, "whatsapp.faq_reply": send,
            "whatsapp.support_answer": send}


def manual_social_executors() -> dict[str, Executor]:
    """Tant que l'outil multi-comptes (Metricool, Buffer…) n'est pas raccordé (phase 2),
    une publication validée est remise à l'équipe pour publication manuelle."""

    def manual(payload: dict) -> dict:
        return {"mode": "manuel", "a_publier_par": "équipe communication",
                "reseau": payload.get("reseau"), "compte": payload.get("compte")}

    return {"social.post": manual, "social.manual_post": manual,
            # La réponse de SARA est renvoyée par l'API ; l'action sert au journal et à l'arrêt.
            "sara.answer": lambda payload: {"canal": "sara"},
            # Le texte est conservé au journal : page « Rapports » du tableau de bord.
            "report.publish": lambda payload: {"publie": "tableau de bord",
                                               "texte": payload.get("text", "")}}


@dataclass
class Runtime:
    settings: Settings
    org: OrgConfig
    sessions: sessionmaker[Session]
    kb: KnowledgeBase
    governor: Governor
    llm: LLM | None
    connectors: dict[str, MailConnector] = field(default_factory=dict)
    web_connectors: dict[str, WebConnector] = field(default_factory=dict)
    whatsapp_clients: dict[str, WhatsAppClient] = field(default_factory=dict)
    # Boîtes raccordées depuis le tableau de bord, et fabrique de leurs connecteurs
    dashboard_boxes: set[str] = field(default_factory=set)
    connector_factory: Callable = field(default=None)
    brevo_factory: Callable = field(default=None)

    @property
    def messagerie(self) -> MessagerieAgent:
        return MessagerieAgent(self.org, self.kb, self.llm, self.governor, self.sessions,
                               self.connectors, support=self.support)

    @property
    def whatsapp(self) -> WhatsAppAgent:
        return WhatsAppAgent(self.org, self.kb, self.llm, self.governor, self.sessions,
                             self.messagerie, support=self.support)

    @property
    def support(self) -> SupportAgent:
        return SupportAgent(self.settings, self.org, self.kb, self.llm, self.governor,
                            self.sessions, set(self.connectors))

    @property
    def communication(self) -> CommunicationAgent:
        return CommunicationAgent(self.org, self.kb, self.llm, self.governor,
                                  directives=self.directives_for)

    @property
    def contenus_web(self) -> ContenusWebAgent:
        return ContenusWebAgent(self.org, self.kb, self.llm, self.governor, self.sessions,
                                directives=self.directives_for)

    @property
    def campaigns(self) -> CampaignWriter:
        return CampaignWriter(self.org, self.kb, self.llm, self.governor,
                              directives=self.directives_for)

    @property
    def planner(self) -> WeeklyPlanner:
        return WeeklyPlanner(self.org, self.kb, self.llm, self.governor, self.sessions)

    def directives_for(self, pole: str) -> str:
        return directives_text(self.sessions, pole)

    @property
    def notifier(self) -> ValidatorNotifier:
        return ValidatorNotifier(self.settings, self.org, self.governor, self.sessions,
                                 set(self.connectors))

    @property
    def commercial(self) -> CommercialAgent:
        return CommercialAgent(self.settings, self.org, self.kb, self.llm, self.governor,
                               self.sessions,
                               {a: c.mailbox for a, c in self.connectors.items()})

    @property
    def revue(self) -> MonthlyReviewer:
        mailbox = self.settings.notification_mailbox
        return MonthlyReviewer(self.governor, self.sessions, self.veille,
                               notification_mailbox=mailbox if mailbox in self.connectors else "",
                               month_spend=getattr(self.llm, "month_spend", None))

    @property
    def veille(self) -> VeilleAgent:
        return VeilleAgent(self.settings, self.org, self.governor, self.sessions,
                           set(self.connectors))

    @property
    def chef(self) -> ChefAgent:
        llm = self.llm if isinstance(self.llm, ClaudeClient) else None
        mailbox = self.settings.notification_mailbox
        return ChefAgent(self.governor, self.sessions, llm,
                         notification_mailbox=mailbox if mailbox in self.connectors else "",
                         dashboard_url=self.settings.dashboard_url)


def emailing_executors(sessions, settings: Settings,
                       client_factory: Callable | None = None) -> dict[str, Executor]:
    # client_factory : fabrique du client (remplacée par un faux client dans les tests)
    """Envoi d'une campagne validée par Brevo ; la clé est relue à chaque envoi (coffre)."""
    from .agents.campagnes import UNSUBSCRIBE_FOOTER
    from .channels.emailing import BrevoClient
    from .configstore import service_value

    def send(payload: dict) -> dict:
        key = service_value(sessions, settings, "brevo_api_key")
        client = (client_factory or BrevoClient)(key)
        html = sanitize_html(payload["contenu_html"])
        preheader = payload.get("pre_entete", "")
        if preheader:
            html = (f'<div style="display:none;max-height:0;overflow:hidden">{preheader}</div>'
                    + html)
        return client.send_campaign(
            name=payload["subject"], subject=payload["subject"],
            html=html + UNSUBSCRIBE_FOOTER, sender_name=payload["expediteur"],
            sender_email=payload["expediteur_mail"], list_ids=[int(payload["liste_id"])])

    return {"campaign.mail": send}


def build_runtime(settings: Settings | None = None, llm: LLM | None = None,
                  connectors: dict[str, MailConnector] | None = None,
                  with_llm: bool = True,
                  web_connectors: dict[str, WebConnector] | None = None,
                  whatsapp_clients: dict[str, WhatsAppClient] | None = None) -> Runtime:
    settings = settings or get_settings()
    org = load_org_config(settings.config_dir, settings.valideur_defaut)
    sessions = open_db(make_engine(settings.database_url))
    kb = KnowledgeBase(settings.knowledge_dir, knowledge_overrides(sessions))
    from_config = connectors is None
    if connectors is None:
        connectors = {m.adresse: connector_for(m) for m in org.mailboxes}
    if web_connectors is None:
        web_connectors = {s.url: c for s in org.sites if (c := web_connector_for(s))}
    if whatsapp_clients is None:
        whatsapp_clients = {n.phone_number_id: WhatsAppClient(n, settings.whatsapp_api_version)
                            for n in org.whatsapp}
    executors = {**mail_executors(connectors), **web_executors(web_connectors),
                 **whatsapp_executors(whatsapp_clients, sessions), **manual_social_executors(),
                 **emailing_executors(sessions, settings)}
    governor = Governor(sessions, executors,
                        approval_timeout=timedelta(hours=settings.approval_timeout_hours))
    if llm is None and with_llm:
        llm = ClaudeClient(settings, sessions)
    from .channels.emailing import BrevoClient

    rt = Runtime(settings, org, sessions, kb, governor, llm, connectors, web_connectors,
                 whatsapp_clients, connector_factory=connector_for, brevo_factory=BrevoClient)
    if from_config:
        sync_mailboxes(rt)
    return rt
