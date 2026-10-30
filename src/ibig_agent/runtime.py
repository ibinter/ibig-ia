"""Assemblage de l'agent : configuration, base, gouvernance, connecteurs et agents."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from .agents.campagnes import CampaignWriter
from .agents.chef import ChefAgent
from .agents.commentaires import CommentWatcher
from .agents.commercial import CommercialAgent
from .agents.communication import CommunicationAgent
from .agents.contenus_web import ContenusWebAgent
from .agents.messagerie import MessagerieAgent
from .agents.notifications import ValidatorNotifier
from .agents.plan import WeeklyPlanner
from .agents.revue import MonthlyReviewer
from .agents.support import SupportAgent
from .agents.validation_whatsapp import WhatsAppValidation, validation_executors
from .agents.veille import VeilleAgent
from .agents.whatsapp import WhatsAppAgent
from .channels.mail import MailConnector, connector_for
from .channels.web import WebConnector, sanitize_html, web_connector_for
from .channels.whatsapp import WINDOW_HOURS, WhatsAppClient
from .config import OrgConfig, Settings, get_settings, load_org_config
from .configstore import (
    directives_text,
    knowledge_overrides,
    sync_mailboxes,
    sync_social_accounts,
    sync_whatsapp,
)
from .db import WhatsAppContact, make_engine, open_db, utcnow
from .governance import Executor, Governor, as_utc
from .knowledge import KnowledgeBase
from .llm import LLM, ClaudeClient
from .publishing import publish_due, social_executors
from .semantic import SemanticIndex

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


def template_param(text: str) -> str:
    """Texte rendu acceptable par Meta pour un paramètre de modèle : ni retour à la ligne,
    ni tabulation, ni plus de trois espaces de suite, 1000 caractères au plus."""
    return " ".join(str(text).split())[:1000]


def whatsapp_executors(clients: dict[str, WhatsAppClient],
                       sessions: sessionmaker[Session],
                       settings: Settings | None = None) -> dict[str, Executor]:
    def send(payload: dict) -> dict:
        client = clients.get(payload["phone_number_id"])
        if client is None:
            raise RuntimeError(f"Numéro WhatsApp non raccordé : {payload['phone_number_id']}")
        with sessions() as s:
            contact = s.get(WhatsAppContact, payload["to"])
        if contact is not None and contact.opted_out:
            raise RuntimeError("contact désinscrit (STOP) : aucun envoi")
        # Règle de Meta, vérifiée au moment de l'envoi : la validation a pu prendre du temps.
        if contact is None or contact.last_inbound_at is None:
            raise RuntimeError("contact inconnu : aucun message reçu de lui, aucun envoi")
        if utcnow() - as_utc(contact.last_inbound_at) > timedelta(hours=WINDOW_HOURS):
            template = settings.whatsapp_reply_template if settings else ""
            if not template:
                raise RuntimeError("fenêtre de 24 h dépassée : répondre avec un modèle validé "
                                   "par Meta ou par téléphone")
            # Hors fenêtre, seul un modèle approuvé par Meta est permis : le texte validé par
            # l'humain y est inséré tel quel (au contact qui nous a écrit, jamais à un autre)
            result = client.send_template(payload["to"], template,
                                          [template_param(payload["texte"])])
            return {**result, "modele": template}
        return client.send_text(payload["to"], payload["texte"])

    return {"whatsapp.reply": send, "whatsapp.ack": send, "whatsapp.faq_reply": send,
            "whatsapp.support_answer": send}


def manual_social_executors() -> dict[str, Executor]:
    """Tant que l'outil multi-comptes (Metricool, Buffer…) n'est pas raccordé (phase 2),
    une publication validée est remise à l'équipe pour publication manuelle."""

    def manual(payload: dict) -> dict:
        return {"mode": "manuel", "a_publier_par": "équipe communication",
                "reseau": payload.get("reseau"), "compte": payload.get("compte")}

    # « social.post » est remplacé par la publication programmée (publishing.py) : manuel
    # tant que le compte n'est pas raccordé.
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
    # Numéros WhatsApp et comptes sociaux ajoutés depuis le tableau de bord
    dashboard_numbers: set[str] = field(default_factory=set)
    dashboard_accounts: set[tuple[str, str]] = field(default_factory=set)
    connector_factory: Callable = field(default=None)
    brevo_factory: Callable = field(default=None)
    mailaby_factory: Callable = field(default=None)
    # Fabrique du client HTTP des réseaux sociaux (remplacée dans les tests)
    social_client_factory: Callable = field(default=None)
    semantic: SemanticIndex = field(default=None)
    # Fabrique du générateur d'images (remplacée dans les tests ; défaut : OpenAI)
    image_factory: Callable = field(default=None)
    # Lecteur des sites publics (remplacé dans les tests)
    site_crawler: object = field(default=None)
    # Planificateur en marche (santé des tâches : prochaines exécutions)
    scheduler: object = field(default=None)

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
    def comments(self) -> CommentWatcher:
        return CommentWatcher(self.settings, self.org, self.sessions, self.llm, self.veille,
                              self.social_client_factory)

    @property
    def wa_validation(self) -> WhatsAppValidation:
        return WhatsAppValidation(self.settings, self.org, self.governor, self.sessions,
                                  self.whatsapp_clients, self.notifier)

    def index_knowledge(self) -> dict:
        """Recherche par le sens : met à jour les vecteurs des passages modifiés."""
        try:
            return self.semantic.sync(self.kb)
        except Exception as exc:  # noqa: BLE001 — la recherche par mots-clés continue
            self.semantic.last_error = str(exc)[:200]
            log.warning("Indexation de la base impossible : %s", exc)
            return {"erreur": str(exc)[:200]}

    def refresh_catalog(self) -> str:
        """Catalogue complet des produits : relu depuis IBIG PARTNERS et le site EDUFORM, puis
        rechargé dans la base de connaissances s'il a changé (tâche du dimanche)."""
        from .catalog_import import import_catalog
        from .configstore import reload_knowledge

        result, stats = import_catalog(self.settings.catalog_dir, sessions=self.sessions)
        if stats.get("ecrits") or stats.get("retires"):
            reload_knowledge(self)
        return f"{result.total} produits ({stats}) · registre {result.register}"

    def import_sites(self, poles: list[str] | None = None) -> dict[str, str]:
        """Import de la base de connaissances depuis les sites publics des pôles."""
        import json

        from .configstore import reload_knowledge, service_value, set_service_value
        from .site_import import SiteImporter

        codes = poles or [p.code for p in self.org.poles if p.site]
        importer = SiteImporter(self.org, self.kb, self.llm, self.sessions,
                                self.site_crawler)
        results = {}
        for code in codes:
            status = json.loads(service_value(self.sessions, self.settings, "import_sites")
                                or "{}")
            status[code] = {"etat": "en_cours", "resume": "", "at": utcnow().isoformat()}
            set_service_value(self.sessions, self.settings, "import_sites",
                              json.dumps(status), "agent")
            try:
                resume, etat = importer.run(code).summary(), "fait"
            except Exception as exc:  # noqa: BLE001 — noté, pôle suivant
                resume, etat = str(exc)[:300], "echec"
            status = json.loads(service_value(self.sessions, self.settings, "import_sites")
                                or "{}")
            status[code] = {"etat": etat, "resume": resume, "at": utcnow().isoformat()}
            set_service_value(self.sessions, self.settings, "import_sites",
                              json.dumps(status), "agent")
            results[code] = resume
        reload_knowledge(self)
        return results

    def publish_due(self) -> int:
        return publish_due(self.governor, self.sessions)

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
    def missions(self):
        from .missions import MissionService

        return MissionService(self.sessions, self.governor, self.org.pole_codes)

    def mailaby_stats(self, days: int) -> dict | None:
        """Statistiques des campagnes si Mailaby est raccordé, sinon None."""
        client, is_mailaby = emailing_client(self.sessions, self.settings,
                                             self.brevo_factory, self.mailaby_factory)
        return client.stats(days) if is_mailaby else None

    @property
    def veille(self) -> VeilleAgent:
        return VeilleAgent(self.settings, self.org, self.governor, self.sessions,
                           set(self.connectors), mailaby_stats=self.mailaby_stats)

    @property
    def chef(self) -> ChefAgent:
        llm = self.llm if isinstance(self.llm, ClaudeClient) else None
        mailbox = self.settings.notification_mailbox
        return ChefAgent(self.governor, self.sessions, llm,
                         notification_mailbox=mailbox if mailbox in self.connectors else "",
                         dashboard_url=self.settings.dashboard_url)


def emailing_client(sessions, settings: Settings, brevo_factory: Callable | None = None,
                    mailaby_factory: Callable | None = None) -> tuple[object, bool]:
    """Service d'emailing réglé au tableau de bord : Mailaby s'il est raccordé (adresse et
    jeton), sinon Brevo. Renvoie (client, est_mailaby) ; les accès sont relus à chaque appel."""
    from .channels.emailing import BrevoClient
    from .channels.mailaby import MailabyClient
    from .configstore import service_value

    url = service_value(sessions, settings, "mailaby_url")
    token = service_value(sessions, settings, "mailaby_token")
    if url and token:
        return (mailaby_factory or MailabyClient)(url, token), True
    return (brevo_factory or BrevoClient)(service_value(sessions, settings,
                                                        "brevo_api_key")), False


def emailing_executors(sessions, settings: Settings,
                       client_factory: Callable | None = None,
                       mailaby_factory: Callable | None = None) -> dict[str, Executor]:
    # client_factory : fabrique du client (remplacée par un faux client dans les tests)
    """Envoi d'une campagne validée par Mailaby ou Brevo ; accès relus à chaque envoi."""
    from .agents.campagnes import UNSUBSCRIBE_FOOTER

    def send(payload: dict) -> dict:
        client, is_mailaby = emailing_client(sessions, settings, client_factory,
                                             mailaby_factory)
        html = sanitize_html(payload["contenu_html"])
        preheader = payload.get("pre_entete", "")
        if preheader:
            html = (f'<div style="display:none;max-height:0;overflow:hidden">{preheader}</div>'
                    + html)
        # Mailaby ajoute lui-même son lien de désinscription et le suivi
        return client.send_campaign(
            name=payload["subject"], subject=payload["subject"],
            html=html if is_mailaby else html + UNSUBSCRIBE_FOOTER,
            sender_name=payload["expediteur"],
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
    try:  # le registre du catalogue fait foi : statuts et validations survivent aux redéploiements
        from .catalog_import import regenerate

        regenerate(settings.catalog_dir, sessions)
    except Exception as exc:  # noqa: BLE001 — l'agent démarre avec les fichiers existants
        log.warning("Catalogue : régénération au démarrage impossible : %s", exc)
    kb = KnowledgeBase(settings.knowledge_dir, knowledge_overrides(sessions),
                       extra_roots=[settings.catalog_dir])
    from_config = connectors is None
    if connectors is None:
        connectors = {m.adresse: connector_for(m) for m in org.mailboxes}
    if web_connectors is None:
        web_connectors = {s.url: c for s in org.sites if (c := web_connector_for(s))}
    whatsapp_given = whatsapp_clients is not None
    if whatsapp_clients is None:
        whatsapp_clients = {n.phone_number_id: WhatsAppClient(n, settings.whatsapp_api_version)
                            for n in org.whatsapp}
    executors = {**mail_executors(connectors), **web_executors(web_connectors),
                 **whatsapp_executors(whatsapp_clients, sessions, settings),
                 **manual_social_executors(),
                 **social_executors(sessions, settings),
                 **validation_executors(whatsapp_clients, settings),
                 **emailing_executors(sessions, settings)}
    governor = Governor(sessions, executors,
                        approval_timeout=timedelta(hours=settings.approval_timeout_hours))
    if llm is None and with_llm:
        llm = ClaudeClient(settings, sessions)
    from .channels import mailaby as mailaby_channel
    from .channels.emailing import BrevoClient
    from .channels.mailaby import MailabyClient

    rt = Runtime(settings, org, sessions, kb, governor, llm, connectors, web_connectors,
                 whatsapp_clients, connector_factory=connector_for, brevo_factory=BrevoClient,
                 mailaby_factory=MailabyClient)
    rt.semantic = SemanticIndex(sessions, settings)
    kb.semantic = rt.semantic.scores

    def _mailaby_client():
        client, is_mailaby = emailing_client(sessions, settings, rt.brevo_factory,
                                             rt.mailaby_factory)
        return client if is_mailaby else None

    mailaby_channel.client_provider = _mailaby_client
    if from_config:
        sync_mailboxes(rt)
    sync_social_accounts(rt)
    if not whatsapp_given:
        sync_whatsapp(rt)
    return rt
