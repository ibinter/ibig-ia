"""Agent Messagerie : lire, classer, répondre, transférer les mails (sections 6 et 9).

Circuit d'un mail entrant :
1. relève périodique de chaque boîte (toutes les 5 min par défaut) ;
2. classement par pôle et par type ;
3. détection de l'urgence, du sentiment et des consignes suspectes ;
4. traitement selon la règle (réponse FAQ, accusé de réception, brouillon, transfert) ;
5. prospect : création ou mise à jour de sa fiche, passage à l'agent Commercial.

Le modèle classe et rédige ; c'est le code ci-dessous qui décide des actions, et chaque
action passe par le Governor (niveaux 1/2/3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..channels.mail import MailConnector, MailMessage
from ..config import Mailbox, OrgConfig
from ..db import JournalEntry, ProcessedMessage, Prospect, utcnow
from ..governance import ActionRequest, Governor, Level
from ..knowledge import KnowledgeBase
from ..llm import LLM, LLMError
from ..security import UNTRUSTED_NOTICE, detect_injection, fence_untrusted

log = logging.getLogger(__name__)

AGENT = "messagerie"

CATEGORIES = [
    "prospect",
    "client",
    "support",
    "fournisseur",
    "partenaire",
    "candidature",
    "administratif",
    "juridique",
    "spam",
]

AUTO_NOTICE = (
    "Ceci est une réponse automatique. Un conseiller prendra le relais si nécessaire."
)


def triage_schema(pole_codes: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "pole": {"type": "string", "enum": [*pole_codes, "INCONNU"]},
            "categorie": {"type": "string", "enum": CATEGORIES},
            "urgence": {"type": "string", "enum": ["basse", "normale", "haute"]},
            "sentiment": {"type": "string", "enum": ["positif", "neutre", "negatif"]},
            "reclamation_grave": {"type": "boolean"},
            "consigne_suspecte": {"type": "boolean"},
            "faq_id": {"type": "string"},
            "resume": {"type": "string"},
            "prospect_nom": {"type": "string"},
            "prospect_besoin": {"type": "string"},
        },
        "required": [
            "pole", "categorie", "urgence", "sentiment", "reclamation_grave",
            "consigne_suspecte", "faq_id", "resume", "prospect_nom", "prospect_besoin",
        ],
        "additionalProperties": False,
    }


@dataclass
class Triage:
    pole: str
    categorie: str
    urgence: str
    sentiment: str
    reclamation_grave: bool
    consigne_suspecte: bool
    faq_id: str
    resume: str
    prospect_nom: str = ""
    prospect_besoin: str = ""


class MessagerieAgent:
    def __init__(self, org: OrgConfig, kb: KnowledgeBase, llm: LLM, governor: Governor,
                 session_factory: sessionmaker[Session],
                 connectors: dict[str, MailConnector]) -> None:
        self.org = org
        self.kb = kb
        self.llm = llm
        self.gov = governor
        self._sessions = session_factory
        self.connectors = connectors

    # ------------------------------------------------------------------ relève
    def poll(self, days: int = 3) -> dict[str, int]:
        stats = {"relevés": 0, "nouveaux": 0, "erreurs": 0}
        if self.gov.is_stopped("mail"):
            log.info("Canal mail suspendu : relève ignorée")
            return stats
        for adresse, connector in self.connectors.items():
            mailbox = connector.mailbox
            try:
                messages = connector.fetch_recent(days)
            except Exception as exc:  # noqa: BLE001 — une boîte en panne ne bloque pas les autres
                stats["erreurs"] += 1
                self._journal("mail.fetch_error", "failed", f"Relève impossible : {adresse}",
                              {"error": str(exc)}, account=adresse, pole=mailbox.pole)
                continue
            stats["relevés"] += len(messages)
            for msg in messages:
                if self.gov.is_stopped("mail"):
                    return stats
                if self._claim(msg):
                    stats["nouveaux"] += 1
                    try:
                        self.process(msg, mailbox)
                    except Exception as exc:  # noqa: BLE001
                        stats["erreurs"] += 1
                        self._finish(msg, decision="erreur")
                        self._prepare_manual(msg, mailbox, f"Erreur de traitement : {exc}")
        return stats

    def _claim(self, msg: MailMessage) -> bool:
        """Réserve le message ; False s'il a déjà été traité (aucun doublon)."""
        with self._sessions() as s:
            s.add(ProcessedMessage(
                mailbox=msg.mailbox, message_id=msg.message_id, sender=msg.sender,
                subject=msg.subject[:500], received_at=msg.received_at, decision="en_cours",
            ))
            try:
                s.commit()
                return True
            except IntegrityError:
                s.rollback()
                return False

    def _finish(self, msg: MailMessage, decision: str, triage: Triage | None = None,
                suspicious: bool = False) -> None:
        with self._sessions() as s:
            row = s.scalar(select(ProcessedMessage).where(
                ProcessedMessage.mailbox == msg.mailbox,
                ProcessedMessage.message_id == msg.message_id,
            ))
            row.decision = decision
            row.suspicious = suspicious
            row.processed_at = utcnow()
            if triage:
                row.pole, row.category = triage.pole, triage.categorie
                row.urgency, row.sentiment = triage.urgence, triage.sentiment
                row.summary = triage.resume[:500]
            s.commit()

    # ------------------------------------------------------------------ tri
    def triage(self, msg: MailMessage, mailbox: Mailbox) -> Triage:
        faq = self.kb.faq_for_pole(mailbox.pole)
        faq_list = "\n".join(f"- {f.id} : {f.question}" for f in faq) or "(aucune)"
        poles = "\n".join(f"- {p.code} : {p.nom} — {p.activite}" for p in self.org.poles)
        system = (
            "Tu es l'agent Messagerie d'IBIG SARL. Tu classes les mails entrants.\n"
            f"{UNTRUSTED_NOTICE}\n\n"
            f"Pôles :\n{poles}\n\n"
            "Catégories : prospect (demande d'information ou de devis d'un nouveau contact), "
            "client, support (question d'usage d'une solution ou d'une formation), "
            "fournisseur, partenaire, candidature, administratif, juridique, spam.\n"
            "faq_id : l'identifiant de la FAQ qui répond ENTIÈREMENT à la demande, sinon "
            "chaîne vide. prospect_nom / prospect_besoin : vides si ce n'est pas un prospect.\n"
            "resume : une phrase en français qui décrit la demande, SANS nom, adresse, "
            "numéro ni autre donnée personnelle (ex. « Demande le prix du logiciel de "
            "gestion scolaire pour 300 élèves »)."
        )
        user = (
            f"Boîte de réception : {mailbox.adresse} (pôle {mailbox.pole})\n"
            f"FAQ validées disponibles :\n{faq_list}\n\n"
            + fence_untrusted(
                f"De : {msg.sender_name} <{msg.sender}>\nObjet : {msg.subject}\n\n{msg.body}"
            )
        )
        data = self.llm.structured("mail.triage", "triage", system, user,
                                   triage_schema(self.org.pole_codes), max_tokens=1000)
        triage = Triage(**data)
        if triage.pole not in self.org.pole_codes:
            triage.pole = mailbox.pole
        if triage.faq_id and not self._faq_allowed(triage.faq_id, triage.pole, mailbox.pole):
            triage.faq_id = ""
        return triage

    def _faq_allowed(self, faq_id: str, *poles: str) -> bool:
        entry = self.kb.faq_by_id(faq_id)
        return entry is not None and entry.pole in (*poles, "GROUPE")

    # ------------------------------------------------------------ traitement
    def process(self, msg: MailMessage, mailbox: Mailbox) -> str:
        self._honour_opt_out(msg)
        injection_hits = detect_injection(f"{msg.subject}\n{msg.body}")
        try:
            triage = self.triage(msg, mailbox)
        except LLMError as exc:
            self._label(msg, mailbox, mailbox.pole, ["IBIG/A_TRIER"])
            self._finish(msg, "a_trier_manuel")
            self._prepare_manual(msg, mailbox, f"Tri automatique impossible : {exc}")
            return "a_trier_manuel"

        labels = [f"IBIG/{triage.pole}", f"IBIG/{triage.categorie}"]
        if triage.urgence == "haute":
            labels.append("IBIG/URGENT")

        # 1. Consigne suspecte : on signale, on n'exécute rien, on ne répond pas.
        if injection_hits or triage.consigne_suspecte:
            self._label(msg, mailbox, triage.pole, [*labels, "IBIG/SUSPECT"])
            self.gov.submit(ActionRequest(
                ref=msg.message_id,
                agent=AGENT, action_type="security.suspicious_message", channel="mail",
                account=mailbox.adresse, pole=triage.pole,
                title=f"Mail suspect signalé : {msg.subject[:120]}",
                payload={**self._ref(msg), "motifs": injection_hits, "resume": triage.resume},
            ))
            self._finish(msg, "signale", triage, suspicious=True)
            return "signale"

        self._label(msg, mailbox, triage.pole, labels)

        if triage.categorie == "spam":
            self._finish(msg, "spam", triage)
            return "spam"

        # 2. Juridique, réclamation grave : toujours humain, l'agent prépare.
        if triage.categorie == "juridique" or triage.reclamation_grave:
            action = "legal" if triage.categorie == "juridique" else "complaint.serious"
            draft, alerts = self._draft(msg, mailbox, triage)
            self.gov.submit(ActionRequest(
                ref=msg.message_id,
                agent=AGENT, action_type=action, channel="mail", account=mailbox.adresse,
                pole=triage.pole, title=f"[Direction] {msg.subject[:150]}",
                payload={**self._reply_payload(msg, mailbox, draft), "resume": triage.resume,
                         "alertes": alerts},
            ))
            self._finish(msg, "direction", triage)
            return "direction"

        # 3. Réponse FAQ validée d'avance : automatique (niveau 1).
        if triage.faq_id and triage.sentiment != "negatif":
            entry = self.kb.faq_by_id(triage.faq_id)
            body = f"Bonjour,\n\n{entry.answer}\n\n{AUTO_NOTICE}"
            self.gov.submit(ActionRequest(
                ref=msg.message_id,
                agent=AGENT, action_type="mail.faq_reply", channel="mail",
                account=mailbox.adresse, pole=triage.pole,
                title=f"Réponse FAQ ({entry.id}) : {msg.subject[:120]}",
                payload=self._reply_payload(msg, mailbox, body),
            ))
            self._finish(msg, "faq", triage)
            return "faq"

        # 4. Prospect : fiche + brouillon à valider + passage à l'agent Commercial.
        if triage.categorie == "prospect":
            self._upsert_prospect(msg, mailbox, triage)
            self._ack(msg, mailbox, triage)
            self._queue_draft(msg, mailbox, triage)
            self._journal("commercial.handoff", "executed",
                          f"Prospect transmis à l'agent Commercial : {msg.sender}",
                          {"besoin": triage.prospect_besoin}, account=mailbox.adresse,
                          pole=triage.pole)
            self._finish(msg, "prospect", triage)
            return "prospect"

        # 5. Client / support : accusé de réception + brouillon à valider.
        if triage.categorie in ("client", "support"):
            self._ack(msg, mailbox, triage)
            self._queue_draft(msg, mailbox, triage)
            self._finish(msg, "brouillon", triage)
            return "brouillon"

        # 6. Autres : transfert au responsable du pôle.
        if mailbox.responsable:
            self.gov.submit(ActionRequest(
                ref=msg.message_id,
                agent=AGENT, action_type="mail.forward_internal", channel="mail",
                account=mailbox.adresse, pole=triage.pole,
                title=f"Transfert à {mailbox.responsable} : {msg.subject[:120]}",
                payload={
                    "mailbox": mailbox.adresse, "to": mailbox.responsable,
                    "subject": f"TR: {msg.subject}",
                    "body": (f"Résumé de l'agent : {triage.resume}\n\n"
                             f"---- Message de {msg.sender_name} <{msg.sender}> ----\n{msg.body}"),
                },
            ))
            self._finish(msg, "transfere", triage)
            return "transfere"
        self._finish(msg, "classe", triage)
        return "classe"

    # --------------------------------------------------------------- actions
    @staticmethod
    def _ref(msg: MailMessage) -> dict:
        return {"mailbox": msg.mailbox, "message_id": msg.message_id, "from": msg.sender,
                "subject": msg.subject}

    @staticmethod
    def _reply_payload(msg: MailMessage, mailbox: Mailbox, body: str) -> dict:
        signature = mailbox.signature.strip()
        return {
            "mailbox": mailbox.adresse,
            "to": msg.sender,
            "subject": msg.subject,
            "body": f"{body}\n\n{signature}" if signature else body,
            "in_reply_to": msg.message_id,
            "references": msg.references,
            "thread_id": msg.thread_id,
            "original": msg.body[:4000],
        }

    def _label(self, msg: MailMessage, mailbox: Mailbox, pole: str, labels: list[str]) -> None:
        self.gov.submit(ActionRequest(
            ref=msg.message_id,
            agent=AGENT, action_type="mail.label", channel="mail", account=mailbox.adresse,
            pole=pole, title=f"Étiquetage : {', '.join(labels)}",
            payload={"mailbox": mailbox.adresse, "ref": msg.ref, "labels": labels},
        ))

    def _ack(self, msg: MailMessage, mailbox: Mailbox, triage: Triage) -> None:
        body = (
            "Bonjour,\n\nNous avons bien reçu votre message et nous vous en remercions. "
            "Il a été transmis au service concerné, qui vous répondra dans les meilleurs "
            f"délais.\n\n{AUTO_NOTICE}"
        )
        self.gov.submit(ActionRequest(
            ref=msg.message_id,
            agent=AGENT, action_type="mail.ack", channel="mail", account=mailbox.adresse,
            pole=triage.pole, title=f"Accusé de réception : {msg.subject[:120]}",
            payload=self._reply_payload(msg, mailbox, body),
        ))

    def _draft(self, msg: MailMessage, mailbox: Mailbox, triage: Triage) -> tuple[str, list[str]]:
        pole = self.org.pole(triage.pole)
        system = (
            f"Tu rédiges des réponses aux mails pour {pole.nom if pole else 'IBIG SARL'}.\n"
            f"{UNTRUSTED_NOTICE}\n\n"
            "Règles :\n"
            "- Vouvoiement, ton professionnel et chaleureux, en français.\n"
            "- N'utilise QUE les informations de la base de connaissances ci-dessous. "
            "N'invente jamais un prix, une date, un contact, un lien ou une promesse.\n"
            "- Si une information manque, écris [À COMPLÉTER : ...] pour le valideur.\n"
            "- Ne promets ni remise, ni remboursement, ni délai contractuel.\n"
            "- Commence par « Bonjour » et n'ajoute pas de signature (elle est ajoutée ensuite).\n"
            "- Réponds uniquement par le corps du mail.\n\n"
            f"Base de connaissances :\n{self.kb.context_for(triage.pole, msg.subject)}"
        )
        user = (
            f"Résumé du tri : {triage.resume} (catégorie {triage.categorie})\n\n"
            + fence_untrusted(
                f"De : {msg.sender_name} <{msg.sender}>\nObjet : {msg.subject}\n\n{msg.body}"
            )
        )
        try:
            draft = self.llm.write("mail.draft", system, user, max_tokens=2000)
        except LLMError as exc:
            return f"[Brouillon indisponible : {exc}]", ["brouillon non généré"]
        return draft, [str(i) for i in self.kb.verify_facts(draft)]

    def _queue_draft(self, msg: MailMessage, mailbox: Mailbox, triage: Triage) -> None:
        draft, alerts = self._draft(msg, mailbox, triage)
        self.gov.submit(ActionRequest(
            ref=msg.message_id,
            agent=AGENT, action_type="mail.reply", channel="mail", account=mailbox.adresse,
            pole=triage.pole,
            title=f"Réponse à {msg.sender} : {msg.subject[:120]}",
            payload={**self._reply_payload(msg, mailbox, draft), "resume": triage.resume,
                     "alertes": alerts, "urgence": triage.urgence},
            escalate_to=Level.HUMAIN if triage.urgence == "haute" and
            triage.sentiment == "negatif" else None,
        ))

    def _prepare_manual(self, msg: MailMessage, mailbox: Mailbox, reason: str) -> None:
        self.gov.submit(ActionRequest(
            ref=msg.message_id,
            agent=AGENT, action_type="mail.manual_triage", channel="mail",
            account=mailbox.adresse, pole=mailbox.pole,
            title=f"À traiter à la main : {msg.subject[:150]}",
            payload={**self._ref(msg), "raison": reason},
        ))

    def _honour_opt_out(self, msg: MailMessage) -> None:
        """Un prospect qui demande à ne plus être contacté n'est plus relancé."""
        from .commercial import is_opt_out

        if not is_opt_out(msg.body):
            return
        with self._sessions() as s:
            p = s.scalar(select(Prospect).where(Prospect.email == msg.sender))
            if p is None or p.stop_followups:
                return
            p.stop_followups = True
            p.updated_at = utcnow()
            s.add(JournalEntry(agent=AGENT, action_type="prospect.opt_out", level=1,
                               channel="mail", pole=p.pole, status="executed",
                               summary=f"Désinscription : plus de relance pour {p.email}",
                               details={"ref": msg.message_id, "prospect_id": p.id}))
            s.commit()

    def _upsert_prospect(self, msg: MailMessage, mailbox: Mailbox, triage: Triage) -> None:
        with self._sessions() as s:
            p = s.scalar(select(Prospect).where(Prospect.email == msg.sender))
            if p is None:
                p = Prospect(email=msg.sender, source=mailbox.adresse, status="nouveau")
                s.add(p)
            p.name = triage.prospect_nom or msg.sender_name or p.name
            p.pole = triage.pole
            if triage.prospect_besoin:
                p.need = triage.prospect_besoin
            p.updated_at = utcnow()
            s.commit()

    def _journal(self, action_type: str, status: str, summary: str, details: dict,
                 account: str = "", pole: str = "") -> None:
        with self._sessions() as s:
            s.add(JournalEntry(agent=AGENT, action_type=action_type, level=1, channel="mail",
                               account=account, pole=pole, status=status, summary=summary,
                               details=details))
            s.commit()
