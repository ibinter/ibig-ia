"""Paiements, commissions et indicateurs financiers (cahier des charges, Lot 5).

Règles :
  - un paiement se rattache à un devis accepté ; il est saisi avec une référence unique par mode
    de paiement et une preuve, puis confirmé par une AUTRE personne (sauf s'il n'y a qu'un seul
    responsable actif : la confirmation par la même personne est alors permise et journalisée) ;
  - seuls les paiements confirmés comptent ; le total confirmé ne dépasse jamais le devis ;
  - quand le devis est payé en totalité, la fiche prospect passe à « payé / livré » ;
  - chaque paiement confirmé fait naître une commission au taux du pôle, à valider par la
    direction puis à marquer comme versée. Le taux appliqué est figé dans la commission.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .db import (
    Commission,
    CommissionRate,
    JournalEntry,
    Payment,
    Prospect,
    Quote,
    User,
    utcnow,
)

METHODS = {"mobile_money": "Mobile Money", "virement": "Virement", "especes": "Espèces",
           "cheque": "Chèque", "autre": "Autre"}
PAYMENT_LABELS = {"a_confirmer": "À confirmer", "confirme": "Confirmé", "rejete": "Rejeté"}
COMMISSION_LABELS = {"a_valider": "À valider", "valide": "Validée", "verse": "Versée",
                     "annule": "Annulée"}


class FinanceError(ValueError):
    pass


class FinanceService:
    def __init__(self, sessions: sessionmaker[Session], pole_codes: set[str],
                 set_stage: Callable[..., None]) -> None:
        self._sessions = sessions
        self.pole_codes = set(pole_codes)
        self._set_stage = set_stage

    def _journal(self, s: Session, action: str, pole: str, text: str, by: str,
                 details: dict | None = None) -> None:
        s.add(JournalEntry(agent="humain", action_type=action, level=3, channel="interne",
                           pole=pole, status="executed", summary=text, details=details or {},
                           decided_by=by))

    def confirmed_total(self, s: Session, quote_id: int) -> int:
        return int(s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.quote_id == quote_id, Payment.status == "confirme")) or 0)

    # ------------------------------------------------------------------ paiements
    def record(self, quote_id: int, amount: int, method: str, reference: str, proof: str,
               by: str) -> int:
        reference = " ".join(reference.split())[:120]
        if method not in METHODS:
            raise FinanceError("Mode de paiement inconnu")
        if amount <= 0:
            raise FinanceError("Le montant doit être positif")
        if not reference:
            raise FinanceError("La référence du paiement est obligatoire")
        if len(proof.strip()) < 3:
            raise FinanceError("Indiquez la preuve (n° de transaction, lien du justificatif…)")
        with self._sessions() as s:
            q = s.get(Quote, quote_id)
            if q is None:
                raise FinanceError(f"Devis {quote_id} introuvable")
            if q.status not in ("accepte", "paye"):
                raise FinanceError("Un paiement se rattache à un devis accepté")
            reste = q.total - self.confirmed_total(s, q.id)
            if amount > reste:
                raise FinanceError(f"Ce montant dépasse le reste à payer ({reste} {q.currency})")
            if s.scalar(select(Payment.id).where(Payment.method == method,
                                                 Payment.reference == reference)):
                raise FinanceError("Cette référence est déjà enregistrée : paiement en double")
            p = Payment(quote_id=q.id, prospect_id=q.prospect_id, pole=q.pole, amount=amount,
                        currency=q.currency, method=method, reference=reference,
                        proof=proof.strip()[:1000], recorded_by=by)
            s.add(p)
            s.flush()
            self._journal(s, "paiement.saisi", q.pole,
                          f"{q.number} : paiement de {amount} {q.currency} saisi "
                          f"({METHODS[method]})", by, {"paiement": p.id})
            s.commit()
            return p.id

    def _sole_manager(self, s: Session) -> bool:
        n = s.scalar(select(func.count()).select_from(User).where(
            User.active.is_(True), User.role.in_(("admin", "direction")))) or 0
        return n <= 1

    def confirm(self, payment_id: int, by: str) -> str:
        with self._sessions() as s:
            p = s.get(Payment, payment_id)
            if p is None:
                raise FinanceError("Paiement introuvable")
            if p.status != "a_confirmer":
                raise FinanceError("Ce paiement est déjà traité")
            same = p.recorded_by == by
            if same and not self._sole_manager(s):
                raise FinanceError("La confirmation doit venir d'une autre personne que celle "
                                   "qui a saisi le paiement")
            q = s.get(Quote, p.quote_id)
            if p.amount > q.total - self.confirmed_total(s, q.id):
                raise FinanceError("Le total confirmé dépasserait le montant du devis")
            now = utcnow()
            p.status, p.confirmed_by, p.confirmed_at = "confirme", by, now
            s.flush()
            self._journal(s, "paiement.confirme", p.pole,
                          f"{q.number} : paiement de {p.amount} {p.currency} confirmé"
                          + (" (même personne : un seul responsable actif)" if same else ""),
                          by, {"paiement": p.id, "meme_personne": same})
            note = self._commission(s, p, q, now)
            fully_paid = self.confirmed_total(s, q.id) >= q.total
            if fully_paid:
                q.status, q.updated_at = "paye", now
            prospect_id, number = q.prospect_id, q.number
            s.commit()
        if fully_paid:
            self._set_stage(prospect_id, "paye_livre", by, f"Devis {number} payé en totalité",
                            confirmed_payment=True)
        return note

    def reject(self, payment_id: int, reason: str, by: str) -> None:
        if len(reason.strip()) < 3:
            raise FinanceError("Indiquez le motif du rejet")
        with self._sessions() as s:
            p = s.get(Payment, payment_id)
            if p is None or p.status != "a_confirmer":
                raise FinanceError("Seul un paiement à confirmer se rejette")
            p.status, p.reject_reason, p.confirmed_by = "rejete", reason.strip()[:300], by
            p.confirmed_at = utcnow()
            self._journal(s, "paiement.rejete", p.pole,
                          f"paiement {p.id} rejeté : {p.reject_reason}", by, {"paiement": p.id})
            s.commit()

    # ------------------------------------------------------------------ commissions
    def set_rate(self, pole: str, percent: float, by: str) -> None:
        if pole not in self.pole_codes:
            raise FinanceError(f"Pôle inconnu : {pole}")
        if not 0 <= percent <= 50:
            raise FinanceError("Le taux est compris entre 0 et 50 %")
        bp = round(percent * 100)
        with self._sessions() as s:
            r = s.get(CommissionRate, pole)
            before = r.rate_bp if r else None
            if r is None:
                s.add(CommissionRate(pole=pole, rate_bp=bp, updated_by=by))
            else:
                r.rate_bp, r.updated_by, r.updated_at = bp, by, utcnow()
            self._journal(s, "commission.taux", pole,
                          f"taux de commission du pôle {pole} : {before} → {bp} points de base",
                          by, {"avant": before, "apres": bp})
            s.commit()

    def rates(self) -> dict[str, int]:
        with self._sessions() as s:
            return {r.pole: r.rate_bp for r in s.scalars(select(CommissionRate)).all()}

    def _commission(self, s: Session, p: Payment, q: Quote, now: datetime) -> str:
        r = s.get(CommissionRate, p.pole)
        if r is None or r.rate_bp <= 0:
            return f"Aucun taux de commission pour le pôle {p.pole or '—'} : pas de commission"
        prospect = s.get(Prospect, q.prospect_id)
        who = (prospect.assigned_to or prospect.assigned_agent) if prospect else ""
        amount = p.amount * r.rate_bp // 10000
        s.add(Commission(payment_id=p.id, quote_id=q.id, pole=p.pole, beneficiary=who or "",
                         base=p.amount, rate_bp=r.rate_bp, amount=amount, created_at=now))
        return f"Commission de {amount} {p.currency} créée (à valider)"

    def commission_step(self, commission_id: int, action: str, by: str) -> None:
        with self._sessions() as s:
            c = s.get(Commission, commission_id)
            if c is None:
                raise FinanceError("Commission introuvable")
            now = utcnow()
            if action == "valider" and c.status == "a_valider":
                c.status, c.validated_by, c.validated_at = "valide", by, now
            elif action == "verser" and c.status == "valide":
                if c.validated_by == by and not self._sole_manager(s):
                    raise FinanceError("Le versement doit être fait par une autre personne que "
                                       "celle qui a validé")
                c.status, c.paid_by, c.paid_at = "verse", by, now
            elif action == "annuler" and c.status in ("a_valider", "valide"):
                c.status = "annule"
            else:
                raise FinanceError("Action impossible dans l'état actuel de la commission")
            self._journal(s, f"commission.{action}", c.pole,
                          f"commission {c.id} ({c.amount}) : {action}", by, {"commission": c.id})
            s.commit()

    # ------------------------------------------------------------------ lecture
    def payments(self, status: str = "", quote_id: int | None = None) -> list[Payment]:
        with self._sessions() as s:
            qry = select(Payment)
            if status:
                qry = qry.where(Payment.status == status)
            if quote_id is not None:
                qry = qry.where(Payment.quote_id == quote_id)
            return list(s.scalars(qry.order_by(Payment.id.desc()).limit(300)).all())

    def commissions(self, status: str = "") -> list[Commission]:
        with self._sessions() as s:
            qry = select(Commission)
            if status:
                qry = qry.where(Commission.status == status)
            return list(s.scalars(qry.order_by(Commission.id.desc()).limit(300)).all())

    def summary(self) -> dict:
        """Chiffre d'affaires confirmé (par pôle et par mois), reste à encaisser, commissions."""
        with self._sessions() as s:
            paid = s.execute(select(Payment.pole, func.sum(Payment.amount)).where(
                Payment.status == "confirme").group_by(Payment.pole)).all()
            months: dict[str, int] = {}
            for at, amount in s.execute(select(Payment.confirmed_at, Payment.amount).where(
                    Payment.status == "confirme")).all():
                if at:
                    key = at.strftime("%Y-%m")
                    months[key] = months.get(key, 0) + amount
            due = 0
            for q in s.scalars(select(Quote).where(Quote.status == "accepte")).all():
                due += q.total - self.confirmed_total(s, q.id)
            by_status = dict(s.execute(select(Commission.status, func.coalesce(
                func.sum(Commission.amount), 0)).group_by(Commission.status)).all())
            to_confirm = s.scalar(select(func.count()).select_from(Payment).where(
                Payment.status == "a_confirmer")) or 0
        return {"by_pole": {p or "—": int(t) for p, t in paid},
                "total": sum(int(t) for _, t in paid), "months": dict(sorted(months.items())),
                "outstanding": due, "commissions": {k: int(v) for k, v in by_status.items()},
                "to_confirm": to_confirm}
