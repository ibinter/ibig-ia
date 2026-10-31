"""Validation depuis WhatsApp (section 12 : « validation en un clic depuis WhatsApp »).

* Chaque nouvel élément de niveau 2 est signalé par WhatsApp au valideur du pôle qui a
  renseigné son numéro (menu Comptes) : « Répondez OK 123 pour valider, NON 123 motif
  pour rejeter ».
* La réponse arrive par le webhook signé de Meta. Seul un numéro enregistré sur un compte
  actif peut décider, et seulement pour les pôles de ce compte ; le niveau 3 reste
  toujours au tableau de bord. Les autres numéros suivent le parcours client habituel.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..auth import Principal, UserStore
from ..channels.whatsapp import InboundMessage, WhatsAppClient
from ..config import OrgConfig, Settings
from ..db import JournalEntry, PendingAction, utcnow
from ..governance import ActionRequest, Executor, GovernanceError, Governor
from .notifications import ValidatorNotifier

AGENT = "chef"
COMMAND = re.compile(r"^\s*(ok|oui|valider|valide|non|rejeter|rejet)\s*(?:n°|no|#)?\s*(\d+)"
                     r"\s*[:,.-]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
APPROVE = {"ok", "oui", "valider", "valide"}


def parse_command(text: str) -> tuple[bool, int, str] | None:
    m = COMMAND.match(text or "")
    if not m:
        return None
    return m.group(1).lower() in APPROVE, int(m.group(2)), m.group(3).strip()[:500]


def validation_executors(clients: dict[str, WhatsAppClient],
                         settings: Settings) -> dict[str, Executor]:
    def send(payload: dict) -> dict:
        client = clients.get(payload.get("phone_number_id", ""))
        if client is None:
            raise RuntimeError("Aucun numéro WhatsApp raccordé pour les validations")
        template = settings.whatsapp_validation_template
        if template and payload.get("modele", True):
            return client.send_template(payload["to"], template, [payload["resume"]])
        return client.send_text(payload["to"], payload["texte"])

    return {"notify.whatsapp": send}


class WhatsAppValidation:
    def __init__(self, settings: Settings, org: OrgConfig, governor: Governor,
                 sessions: sessionmaker[Session], clients: dict[str, WhatsAppClient],
                 notifier: ValidatorNotifier) -> None:
        self.settings, self.org, self.gov = settings, org, governor
        self._sessions, self.clients, self.notifier = sessions, clients, notifier
        self.users = UserStore(sessions, org)

    @property
    def number_id(self) -> str:
        wanted = self.settings.whatsapp_validation_number
        if wanted in self.clients:
            return wanted
        return next(iter(self.clients), "")

    # ------------------------------------------------------------ réponses entrantes
    def handle(self, msg: InboundMessage) -> bool:
        """True si le message venait d'un valideur (et a été traité ici)."""
        who = self.users.by_phone(msg.wa_id)
        if who is None:
            return False
        reply = self.decide(who, msg.text)
        self._send(who, msg.wa_id, reply, pnid=msg.phone_number_id, template=False)
        return True

    def decide(self, who: Principal, text: str) -> str:
        command = parse_command(text)
        if command is None:
            return self._help(who)
        approve, pid, note = command
        with self._sessions() as s:
            pa = s.get(PendingAction, pid)
        if pa is None or not who.can_decide(pa):
            return f"N°{pid} : élément introuvable pour vos pôles."
        if pa.level != 2:
            return f"N°{pid} : dossier réservé à un humain, à traiter au tableau de bord."
        by = f"{who.label} (WhatsApp)"
        try:
            if approve:
                out = self.gov.approve(pid, by=by)
                if out.status == "executed":
                    return f"N°{pid} validé : {pa.title}. C'est parti."
                return f"N°{pid} validé, mais l'exécution a échoué : {out.error[:200]}"
            self.gov.reject(pid, by=by, reason=note or "Rejeté par WhatsApp")
            return f"N°{pid} rejeté : {pa.title}."
        except GovernanceError as exc:
            return f"N°{pid} : refusé — {exc}"

    def _help(self, who: Principal) -> str:
        with self._sessions() as s:
            rows = s.scalars(select(PendingAction).where(
                PendingAction.status == "pending", PendingAction.level == 2)
                .order_by(PendingAction.id)).all()
        mine = [pa for pa in rows if who.can_decide(pa)][:10]
        lines = ["Pour décider : OK 123 (valider) ou NON 123 motif (rejeter)."]
        if mine:
            lines += ["", "En attente :"] + [f"{pa.id} · [{pa.pole}] {pa.title[:80]}"
                                            for pa in mine]
        else:
            lines.append("Rien à valider pour le moment.")
        lines.append(f"Détails : {self.settings.dashboard_url.rstrip('/')}/validations")
        return "\n".join(lines)

    # ------------------------------------------------------------ alertes sortantes
    def _already_sent(self, since: datetime) -> set[str]:
        with self._sessions() as s:
            rows = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "notify.whatsapp",
                JournalEntry.status == "executed", JournalEntry.created_at >= since)).all()
        return {(r.details or {}).get("ref", "") for r in rows}

    def notify(self, now: datetime | None = None) -> int:
        """Signale chaque nouvel élément de niveau 2 aux valideurs qui ont un numéro."""
        if not self.number_id or self.gov.is_stopped("whatsapp"):
            return 0  # canal suspendu : on réessaiera à sa réactivation, sans remplir le journal
        now = now or utcnow()
        done = self._already_sent(now - timedelta(days=14))
        with self._sessions() as s:
            items = s.scalars(select(PendingAction).where(
                PendingAction.status == "pending", PendingAction.level == 2,
                PendingAction.created_at >= now - timedelta(days=7))).all()
        sent = 0
        for pa in items:
            for email in self.notifier._recipients(pa, escalation=False):
                who = self.users.by_email(email)
                phone = self._phone(who)
                if not phone or not who.can_decide(pa) or f"{pa.id}:{phone}" in done:
                    continue
                excerpt = " ".join(str(pa.payload.get("texte") or pa.payload.get("body")
                                       or pa.payload.get("resume") or "").split())[:300]
                summary = f"n°{pa.id} [{pa.pole or '—'}] {pa.title[:120]}"
                text = (f"IBIG — à valider {summary}\n\n{excerpt}\n\n"
                        f"Répondez OK {pa.id} pour valider, ou NON {pa.id} suivi du motif.")
                if self._send(who, phone, text, summary=summary, ref=f"{pa.id}:{phone}",
                              pole=pa.pole):
                    sent += 1
        return sent

    def _phone(self, who: Principal | None) -> str:
        if who is None:
            return ""
        from ..db import User

        with self._sessions() as s:
            user = s.get(User, who.id)
        return user.phone if user is not None else ""

    def _send(self, who: Principal, to: str, text: str, pnid: str = "", summary: str = "",
              ref: str = "", pole: str = "", template: bool = True) -> bool:
        out = self.gov.submit(ActionRequest(
            agent=AGENT, action_type="notify.whatsapp", channel="whatsapp",
            account=who.email, pole=pole, ref=ref,
            title=f"WhatsApp → {who.name} : {(summary or text)[:120]}",
            payload={"phone_number_id": pnid if pnid in self.clients else self.number_id,
                     "to": to, "texte": text, "resume": summary or text[:200],
                     "modele": template}))
        return out.status == "executed"
