"""Agent Support : répondre sur les 14 solutions et les formations (sections 6 et 7).

« Automatique si la réponse est dans la base, sinon escalade. » Pour qu'une réponse
parte sans validation, TOUTES ces conditions sont vérifiées par le code :

1. le modèle déclare la question entièrement couverte par les passages fournis ;
2. chaque affirmation s'appuie sur une citation, et chaque citation figure MOT POUR MOT
   dans le passage cité (vérifié ici, pas par le modèle) ;
3. aucun prix, contact, lien ou pourcentage absent de la base ;
4. chaque document cité a été validé par un humain pour les réponses automatiques
   (`reponses_auto: true` dans son en-tête) ;
5. la demande n'est ni suspecte, ni sensible (mécontentement, urgence) ;
6. le canal n'est pas suspendu.

Sinon : brouillon à valider (niveau 2) si la réponse est documentée, ticket au support
du pôle sinon. SARA, l'assistante des solutions IBIG SOFT, utilise le même moteur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from ..config import OrgConfig, Settings
from ..db import JournalEntry, Ticket, User, utcnow
from ..governance import ActionRequest, Governor
from ..knowledge import KnowledgeBase, Passage, normalize_quote
from ..llm import LLM, LLMError
from ..security import UNTRUSTED_NOTICE, detect_injection, fence_untrusted

AGENT = "support"
AUTO, DRAFT, ESCALATE = "automatique", "brouillon", "escalade"


def answer_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "repondable": {"type": "boolean"},
            "reponse": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"passage_id": {"type": "string"},
                                   "extrait": {"type": "string"}},
                    "required": ["passage_id", "extrait"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["repondable", "reponse", "citations"],
        "additionalProperties": False,
    }


@dataclass
class SupportAnswer:
    status: str  # automatique | brouillon | escalade
    reponse: str = ""
    sources: list[dict] = field(default_factory=list)
    raisons: list[str] = field(default_factory=list)
    ticket_id: int | None = None


class SupportAgent:
    def __init__(self, settings: Settings, org: OrgConfig, kb: KnowledgeBase, llm: LLM,
                 governor: Governor, session_factory: sessionmaker[Session],
                 connected_mailboxes: set[str]) -> None:
        self.settings = settings
        self.org = org
        self.kb = kb
        self.llm = llm
        self.gov = governor
        self._sessions = session_factory
        self.connected_mailboxes = connected_mailboxes

    # ------------------------------------------------------------------ moteur
    def answer(self, question: str, pole: str, sensitive: bool = False) -> SupportAnswer:
        """Réponse documentée ; ne crée ni ticket ni envoi (voir handle_* pour cela)."""
        if detect_injection(question):
            return SupportAnswer(ESCALATE, raisons=["consigne suspecte dans la question"])
        passages = self.kb.search_passages(question, pole)
        if not passages:
            return SupportAnswer(ESCALATE, raisons=["aucun passage de la base ne correspond"])
        pole_obj = self.org.pole(pole)
        listing = "\n\n".join(f"[{p.id}] {p.doc.titre} — {p.heading}\n{p.text}"
                              for p in passages)
        system = (
            "Tu es l'agent Support d'IBIG SARL (SARA pour les clients des solutions IBIG SOFT)"
            f", pôle {pole_obj.nom if pole_obj else pole}.\n{UNTRUSTED_NOTICE}\n\n"
            "Réponds UNIQUEMENT à partir des passages fournis. Chaque affirmation de ta "
            "réponse doit s'appuyer sur une citation : `extrait` est un morceau recopié MOT "
            "POUR MOT du passage (une phrase suffit), `passage_id` son identifiant entre "
            "crochets. Si les passages ne répondent pas entièrement à la question, mets "
            "repondable à false et laisse reponse vide : ne devine jamais.\n"
            "Style : vouvoiement, clair, étapes numérotées pour une procédure, 150 mots "
            "maximum, sans formule d'appel (« Bonjour ») ni signature : elles sont "
            "ajoutées selon le canal."
        )
        user = f"Passages disponibles :\n\n{listing}\n\n" + fence_untrusted(question)
        try:
            data = self.llm.structured("support.answer", "writing", system, user,
                                       answer_schema(), max_tokens=3000)
        except LLMError as exc:
            return SupportAnswer(ESCALATE, raisons=[f"réponse impossible : {exc}"])

        if not data["repondable"] or not data["reponse"].strip():
            return SupportAnswer(ESCALATE, raisons=["la base ne couvre pas la question"])
        return self._check(data, {p.id: p for p in passages}, sensitive)

    def _check(self, data: dict, offered: dict[str, Passage], sensitive: bool) -> SupportAnswer:
        reasons: list[str] = []
        sources, cited = [], []
        for c in data["citations"]:
            p = offered.get(c["passage_id"])
            if p is None:
                reasons.append(f"citation d'un passage non fourni : {c['passage_id']}")
            elif normalize_quote(c["extrait"]) not in normalize_quote(p.text) or len(
                    normalize_quote(c["extrait"])) < 12:
                reasons.append(f"citation introuvable dans {p.id} : « {c['extrait'][:80]} »")
            else:
                cited.append(p)
                sources.append({"id": p.id, "titre": p.doc.titre, "section": p.heading,
                                "extrait": c["extrait"]})
        if not data["citations"]:
            reasons.append("aucune citation")
        reasons += [f"absent de la base — {i}" for i in self.kb.verify_facts(data["reponse"])]
        if not sources:
            return SupportAnswer(ESCALATE, raisons=reasons or ["aucune source vérifiée"])
        auto_blockers = list(reasons)
        auto_blockers += [f"{p.doc.path} non validé pour les réponses automatiques"
                          for p in {p.id: p for p in cited}.values() if not p.auto_ok]
        if sensitive:
            auto_blockers.append("demande sensible (mécontentement ou urgence)")
        status = AUTO if not auto_blockers else DRAFT
        return SupportAnswer(status, data["reponse"].strip(), sources, auto_blockers)

    # ------------------------------------------------------------------ SARA (chat)
    def handle_chat(self, question: str, pole: str, contact: str = "",
                    conversation: str = "") -> SupportAnswer:
        """Question posée à SARA : réponse immédiate si automatique, sinon ticket."""
        if self.gov.is_stopped("sara"):
            result = SupportAnswer(ESCALATE, raisons=["canal SARA suspendu"])
        else:
            result = self.answer(question, pole)
        if result.status == AUTO:
            out = self.gov.submit(ActionRequest(
                agent=AGENT, action_type="sara.answer", channel="sara", pole=pole,
                ref=conversation, title=f"SARA : {question[:120]}",
                payload={"question": question[:2000], "reponse": result.reponse,
                         "sources": result.sources},
            ))
            if out.status == "executed":
                return result
            result = SupportAnswer(ESCALATE, raisons=["réponse bloquée (canal suspendu)"])
        result.ticket_id = self.open_ticket("sara", contact, pole, question, result,
                                            ref=conversation)
        return result

    # ------------------------------------------------------------------ tickets
    def _support_contacts(self, pole_code: str) -> list[str]:
        pole = self.org.pole(pole_code)
        if pole and (pole.support or pole.valideur):
            return [pole.support or pole.valideur]
        from sqlalchemy import select

        with self._sessions() as s:
            return list(s.scalars(select(User.email).where(
                User.role.in_(("direction", "admin")), User.active.is_(True))).all())

    def open_ticket(self, channel: str, contact: str, pole: str, question: str,
                    result: SupportAnswer, ref: str = "") -> int:
        with self._sessions() as s:
            t = Ticket(channel=channel, contact=contact[:300], pole=pole,
                       question=question[:5000], reason="; ".join(result.raisons)[:2000],
                       draft=result.reponse, sources=result.sources, ref=ref[:500])
            s.add(t)
            s.flush()
            s.add(JournalEntry(agent=AGENT, action_type="support.ticket", level=1,
                               channel=channel, pole=pole, status="flagged",
                               summary=f"Ticket n° {t.id} : {question[:120]}",
                               details={"ticket_id": t.id, "ref": ref}))
            s.commit()
            ticket_id = t.id
        self._notify(ticket_id, channel, contact, pole, question, result)
        return ticket_id

    def _notify(self, ticket_id: int, channel: str, contact: str, pole: str, question: str,
                result: SupportAnswer) -> None:
        mailbox = self.settings.notification_mailbox
        if not mailbox or mailbox not in self.connected_mailboxes:
            return
        url = self.settings.dashboard_url.rstrip("/") + "/tickets"
        body = (f"Bonjour,\n\nNouveau ticket n° {ticket_id} ({channel}), pôle {pole}.\n"
                f"Contact : {contact or 'non communiqué'}\n\nQuestion :\n{question}\n\n"
                f"Pourquoi l'agent n'a pas répondu seul : {'; '.join(result.raisons)}\n")
        if result.reponse:
            body += f"\nProposition de réponse (à vérifier) :\n{result.reponse}\n"
        body += f"\nTickets : {url}\n\nMessage envoyé automatiquement par l'agent IA IBIG."
        for to in self._support_contacts(pole):
            self.gov.submit(ActionRequest(
                agent=AGENT, action_type="notify.internal", channel="interne",
                account=mailbox, pole=pole, title=f"Ticket n° {ticket_id} → {to}",
                payload={"mailbox": mailbox, "to": to,
                         "subject": f"[IBIG] Ticket support n° {ticket_id} ({pole})",
                         "body": body},
            ))

    def resolve(self, ticket_id: int, by: str) -> None:
        with self._sessions() as s:
            t = s.get(Ticket, ticket_id)
            if t is None:
                raise ValueError(f"Ticket {ticket_id} introuvable")
            if t.status != "ouvert":
                raise ValueError(f"Ticket {ticket_id} déjà résolu")
            t.status, t.resolved_by, t.resolved_at = "resolu", by, utcnow()
            s.add(JournalEntry(agent="humain", action_type="support.resolve", level=2,
                               channel=t.channel, pole=t.pole, status="executed",
                               summary=f"Ticket n° {t.id} résolu",
                               details={"ticket_id": t.id}, decided_by=by))
            s.commit()
