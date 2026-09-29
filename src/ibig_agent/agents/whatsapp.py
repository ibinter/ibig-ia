"""Messages WhatsApp entrants (section 10), traités comme les mails, en plus court.

Même tri (pôle du numéro, type, urgence, sentiment, consigne suspecte) et mêmes
niveaux de validation que la Messagerie :

* consigne suspecte : signalée, aucune réponse (niveau 3) ;
* « STOP » : le contact n'est plus jamais relancé ni sollicité ;
* FAQ validée ou réponse documentée du Support (citations vérifiées, guide validé) :
  réponse automatique ;
* sinon : court accusé de réception (au plus un toutes les 12 h) et brouillon à valider ;
* juridique, réclamation grave : dossier pour la direction (niveau 3).

Toute réponse vérifie au moment de l'envoi la fenêtre de 24 h de Meta : une réponse
validée trop tard n'est pas envoyée (il faudrait un modèle validé par Meta).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from ..channels.mail import MailMessage
from ..channels.whatsapp import InboundMessage
from ..config import Mailbox, OrgConfig
from ..db import JournalEntry, WhatsAppContact, utcnow
from ..governance import ActionRequest, Governor, Level, as_utc
from ..knowledge import KnowledgeBase
from ..llm import LLM, LLMError
from ..security import UNTRUSTED_NOTICE, detect_injection, fence_untrusted
from .commercial import is_opt_out
from .messagerie import MessagerieAgent, Triage

AGENT = "messagerie"  # même agent, autre canal : mêmes droits
ACK_TEXT = ("Bonjour, merci pour votre message. Un conseiller IBIG vous répond dans les "
            "meilleurs délais.")
AUTO_NOTICE = "(Réponse automatique — un conseiller peut prendre le relais.)"
ACK_EVERY = timedelta(hours=12)


class WhatsAppAgent:
    def __init__(self, org: OrgConfig, kb: KnowledgeBase, llm: LLM, governor: Governor,
                 session_factory: sessionmaker[Session], messagerie: MessagerieAgent,
                 support=None) -> None:
        self.org = org
        self.kb = kb
        self.llm = llm
        self.gov = governor
        self._sessions = session_factory
        self.messagerie = messagerie
        self.support = support
        self.numbers = {n.phone_number_id: n for n in org.whatsapp}

    # ------------------------------------------------------------------ entrée
    def handle(self, inbound: InboundMessage) -> str:
        number = self.numbers.get(inbound.phone_number_id)
        if number is None:
            self._journal("whatsapp.unknown_number", "failed",
                          f"Message reçu sur un numéro non déclaré ({inbound.phone_number_id})",
                          {})
            return "numero_inconnu"
        msg = MailMessage(mailbox=f"whatsapp:{number.phone_number_id}",
                          message_id=inbound.message_id, ref=inbound.message_id,
                          sender=inbound.wa_id, sender_name=inbound.name,
                          subject=f"WhatsApp {number.nom}", body=inbound.text,
                          received_at=inbound.received_at)
        if not self.messagerie._claim(msg):
            return "doublon"  # Meta renvoie parfois la même notification
        contact = self._touch_contact(inbound, number.pole)
        try:
            return self._process(msg, inbound, number.pole, contact)
        except Exception as exc:  # noqa: BLE001 — jamais de message perdu
            self.messagerie._finish(msg, "erreur")
            self._prepare(msg, number.pole, "whatsapp.manual", f"Erreur de traitement : {exc}")
            return "erreur"

    def _process(self, msg: MailMessage, inbound: InboundMessage, pole: str,
                 contact: WhatsAppContact) -> str:
        if self.gov.is_stopped("whatsapp"):
            self.messagerie._finish(msg, "canal_suspendu")
            self._prepare(msg, pole, "whatsapp.manual", "Canal WhatsApp suspendu : à traiter "
                                                        "à la main")
            return "canal_suspendu"
        if is_opt_out(inbound.text):
            self._set_contact(inbound.wa_id, opted_out=True, marketing_opt_in=False)
            self._journal("whatsapp.opt_out", "executed",
                          f"Désinscription WhatsApp : {inbound.wa_id}", {"ref": msg.message_id})
            self.messagerie._finish(msg, "desinscription")
            return "desinscription"
        if inbound.type != "text" or not inbound.text.strip():
            self._ack(msg, pole, contact)
            self._prepare(msg, pole, "whatsapp.manual",
                          f"Message « {inbound.type} » : à consulter dans WhatsApp")
            self.messagerie._finish(msg, "media")
            return "media"

        mailbox = Mailbox(adresse=msg.mailbox, hebergeur="whatsapp", pole=pole)
        hits = detect_injection(inbound.text)
        try:
            triage = self.messagerie.triage(msg, mailbox)
        except LLMError as exc:
            self._ack(msg, pole, contact)
            self._prepare(msg, pole, "whatsapp.manual", f"Tri automatique impossible : {exc}")
            self.messagerie._finish(msg, "a_trier_manuel")
            return "a_trier_manuel"

        if hits or triage.consigne_suspecte:
            self.gov.submit(ActionRequest(
                agent=AGENT, action_type="security.suspicious_message", channel="whatsapp",
                pole=triage.pole, ref=msg.message_id,
                title=f"Message WhatsApp suspect de {inbound.wa_id}",
                payload={"from": inbound.wa_id, "motifs": hits, "resume": triage.resume},
            ))
            self.messagerie._finish(msg, "signale", triage, suspicious=True)
            return "signale"
        if triage.categorie == "spam":
            self.messagerie._finish(msg, "spam", triage)
            return "spam"
        if triage.categorie == "juridique" or triage.reclamation_grave:
            self._ack(msg, triage.pole, contact)
            self.gov.submit(ActionRequest(
                agent=AGENT, channel="whatsapp", pole=triage.pole, ref=msg.message_id,
                action_type="legal" if triage.categorie == "juridique" else "complaint.serious",
                title=f"[Direction] WhatsApp de {inbound.wa_id}",
                payload={"from": inbound.wa_id, "resume": triage.resume,
                         "original": inbound.text},
            ))
            self.messagerie._finish(msg, "direction", triage)
            return "direction"

        sensitive = triage.sentiment == "negatif" or triage.urgence == "haute"
        # Réponse automatique : FAQ validée, ou réponse documentée du Support.
        if triage.faq_id and not sensitive:
            entry = self.kb.faq_by_id(triage.faq_id)
            if self._reply(msg, triage.pole, "whatsapp.faq_reply",
                           f"{entry.answer}\n\n{AUTO_NOTICE}", f"FAQ {entry.id}"):
                self.messagerie._finish(msg, "faq", triage)
                return "faq"
        draft, sources, alerts = "", [], []
        if triage.categorie in ("support", "client") and self.support is not None:
            from .support import AUTO, DRAFT

            result = self.support.answer(inbound.text, triage.pole, sensitive)
            if result.status == AUTO and self._reply(
                    msg, triage.pole, "whatsapp.support_answer",
                    f"{result.reponse}\n\n{AUTO_NOTICE}", "réponse documentée"):
                self.messagerie._finish(msg, "support_auto", triage)
                return "support_auto"
            if result.status in (AUTO, DRAFT):
                draft, sources, alerts = result.reponse, result.sources, result.raisons
        if not draft:
            draft, alerts = self._draft(inbound.text, triage)
        self._ack(msg, triage.pole, contact)
        self.gov.submit(ActionRequest(
            agent=AGENT, action_type="whatsapp.reply", channel="whatsapp", pole=triage.pole,
            ref=msg.message_id, title=f"Réponse WhatsApp à {inbound.name or inbound.wa_id}",
            payload={"phone_number_id": inbound.phone_number_id, "to": inbound.wa_id,
                     "texte": draft, "original": inbound.text, "resume": triage.resume,
                     "sources": sources, "alertes": alerts, "urgence": triage.urgence},
            escalate_to=Level.HUMAIN if triage.urgence == "haute"
            and triage.sentiment == "negatif" else None,
        ))
        if triage.categorie == "prospect":
            self._journal("commercial.handoff", "executed",
                          f"Prospect WhatsApp transmis à l'agent Commercial : {inbound.wa_id}",
                          {"besoin": triage.prospect_besoin, "ref": msg.message_id},
                          pole=triage.pole)
        self.messagerie._finish(msg, "brouillon", triage)
        return "brouillon"

    # ------------------------------------------------------------------ actions
    def _reply(self, msg: MailMessage, pole: str, action: str, text: str, what: str) -> bool:
        out = self.gov.submit(ActionRequest(
            agent=AGENT, action_type=action, channel="whatsapp", pole=pole,
            ref=msg.message_id, title=f"WhatsApp {what} → {msg.sender}",
            payload={"phone_number_id": msg.mailbox.removeprefix("whatsapp:"),
                     "to": msg.sender, "texte": text},
        ))
        return out.status == "executed"

    def _ack(self, msg: MailMessage, pole: str, contact: WhatsAppContact) -> None:
        if contact.last_ack_at and utcnow() - as_utc(contact.last_ack_at) < ACK_EVERY:
            return
        if self._reply(msg, pole, "whatsapp.ack", ACK_TEXT, "accusé de réception"):
            self._set_contact(contact.wa_id, last_ack_at=utcnow())

    def _draft(self, text: str, triage: Triage) -> tuple[str, list[str]]:
        pole = self.org.pole(triage.pole)
        system = (
            f"Tu rédiges une réponse WhatsApp pour {pole.nom if pole else 'IBIG SARL'}.\n"
            f"{UNTRUSTED_NOTICE}\n\nRègles : vouvoiement, 60 mots maximum, ton chaleureux, "
            "sans signature. N'utilise QUE la base de connaissances : aucun prix, date, "
            "contact ou promesse qui n'y figure pas ; sinon écris [À COMPLÉTER : …].\n\n"
            f"Base de connaissances :\n{self.kb.context_for(triage.pole, text)}"
        )
        try:
            draft = self.llm.write("whatsapp.draft", system, fence_untrusted(text),
                                   max_tokens=800)
        except LLMError as exc:
            return "", [f"brouillon non généré : {exc}"]
        return draft, [str(i) for i in self.kb.verify_facts(draft)]

    def _prepare(self, msg: MailMessage, pole: str, action: str, reason: str) -> None:
        self.gov.submit(ActionRequest(
            agent=AGENT, action_type=action, channel="whatsapp", pole=pole,
            ref=msg.message_id, title=f"WhatsApp de {msg.sender_name or msg.sender} : {reason}",
            payload={"from": msg.sender, "raison": reason, "original": msg.body},
        ))

    # ------------------------------------------------------------------ contacts
    def _touch_contact(self, inbound: InboundMessage, pole: str) -> WhatsAppContact:
        with self._sessions() as s:
            c = s.get(WhatsAppContact, inbound.wa_id) or WhatsAppContact(wa_id=inbound.wa_id)
            c.name = inbound.name or c.name
            c.phone_number_id, c.pole = inbound.phone_number_id, pole
            c.last_inbound_at, c.updated_at = inbound.received_at, utcnow()
            s.add(c)
            s.commit()
            return c

    def _set_contact(self, wa_id: str, **values) -> None:
        with self._sessions() as s:
            c = s.get(WhatsAppContact, wa_id)
            for k, v in values.items():
                setattr(c, k, v)
            c.updated_at = utcnow()
            s.commit()

    def _journal(self, action: str, status: str, summary: str, details: dict,
                 pole: str = "") -> None:
        with self._sessions() as s:
            s.add(JournalEntry(agent=AGENT, action_type=action, level=1, channel="whatsapp",
                               pole=pole, status=status, summary=summary, details=details))
            s.commit()

