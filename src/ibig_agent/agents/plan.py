"""Plan de la semaine de l'agent chef (section 6) : à partir des consignes de la
direction et de l'état réel (validations, prospects, démos, fiches), il répartit le
travail entre les agents. Publié dans les rapports ; l'agent chef n'exécute rien lui-même.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import OrgConfig
from ..configstore import active_directives
from ..db import PendingAction, Prospect, Ticket, utcnow
from ..governance import ActionRequest, Governor
from ..knowledge import KnowledgeBase
from ..llm import LLM, LLMError

AGENT = "chef"


class WeeklyPlanner:
    def __init__(self, org: OrgConfig, kb: KnowledgeBase, llm: LLM | None, governor: Governor,
                 session_factory: sessionmaker[Session]) -> None:
        self.org, self.kb, self.llm, self.gov = org, kb, llm, governor
        self._sessions = session_factory

    def facts(self, week_start: date) -> str:
        now = utcnow()
        week_end = week_start + timedelta(days=7)
        with self._sessions() as s:
            pending = dict(s.execute(select(PendingAction.pole, func.count()).where(
                PendingAction.status == "pending").group_by(PendingAction.pole)).all())
            prepared = s.scalar(select(func.count()).select_from(PendingAction).where(
                PendingAction.status == "prepared")) or 0
            hot = s.scalars(select(Prospect).where(
                Prospect.temperature == "chaud",
                Prospect.status.not_in(("gagne", "perdu")))).all()
            demos = s.scalars(select(Prospect).where(Prospect.demo_at >= now)).all()
            trials = s.scalars(select(Prospect).where(Prospect.trial_ends_at >= now)).all()
            tickets = s.scalar(select(func.count()).select_from(Ticket).where(
                Ticket.status == "ouvert")) or 0
        fiches = {d.pole: d.meta.get("statut") != "a_completer" for d in self.kb.documents
                  if d.type == "fiche_pole"}
        lines = [f"Semaine du {week_start:%d/%m/%Y} au {week_end - timedelta(days=1):%d/%m/%Y}",
                 "Pôles dont la fiche est complète : "
                 + (", ".join(p for p, ok in fiches.items() if ok) or "aucun"),
                 "Pôles dont la fiche est à compléter : "
                 + (", ".join(p for p, ok in fiches.items() if not ok) or "aucun"),
                 "Validations en attente par pôle : "
                 + (", ".join(f"{p or '—'} {n}" for p, n in pending.items()) or "aucune"),
                 f"Dossiers réservés à un humain : {prepared}",
                 f"Tickets du support ouverts : {tickets}",
                 f"Prospects chauds en cours : {len(hot)}",
                 "Démonstrations prévues : " + (", ".join(
                     f"{p.pole} {p.demo_at:%d/%m}" for p in demos) or "aucune"),
                 "Essais en cours : " + (", ".join(
                     f"{p.pole} jusqu'au {p.trial_ends_at:%d/%m}" for p in trials) or "aucun")]
        return "\n".join(lines)

    def build(self, week_start: date) -> str:
        consignes = active_directives(self._sessions)
        consignes_txt = "\n".join(
            f"- {'[' + c.pole + '] ' if c.pole else ''}{c.text}" for c in consignes) or "- aucune"
        facts = self.facts(week_start)
        if self.llm is not None:
            system = (
                "Tu es l'agent chef de l'agent IA d'IBIG SARL. Tu planifies, répartis et "
                "contrôles ; tu n'exécutes rien toi-même. Agents disponibles : Communication "
                "(calendrier éditorial, publications), Contenus web (articles), Messagerie "
                "(mails), Commercial (prospects, relances, démos, essais), Support et SARA, "
                "Veille. Rédige le plan de la semaine en français, concret et court : "
                "priorités, puis une section par agent (ce qu'il fera, pour quels pôles), "
                "puis ce qui est attendu des humains (validations, fiches à compléter). "
                "N'invente aucun chiffre, prix ni promesse : appuie-toi sur les faits fournis."
            )
            user = f"Consignes de la direction :\n{consignes_txt}\n\nFaits :\n{facts}"
            try:
                return self.llm.write("chef.weekly_plan", system, user, max_tokens=3000)
            except LLMError:
                pass  # repli : plan factuel sans rédaction
        return (f"Plan de la semaine (sans rédaction automatique)\n\nConsignes de la "
                f"direction :\n{consignes_txt}\n\nÉtat au démarrage :\n{facts}")

    def run(self, week_start: date) -> str:
        text = self.build(week_start)
        self.gov.submit(ActionRequest(
            agent=AGENT, action_type="report.publish", channel="rapport",
            title=f"Plan de la semaine du {week_start:%d/%m/%Y}", payload={"text": text}))
        return text
