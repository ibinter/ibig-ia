"""Devis (cahier des charges, Lot 4) : brouillon → validé → envoyé → accepté / refusé / expiré.

Règles : un prix de catalogue n'est repris que pour une offre « active » à tarif unique ; sinon la
ligne exige un prix saisi à la main, signalé comme tel. Un devis n'est validé que si les prix du
catalogue n'ont pas changé depuis la saisie. L'acceptation ou le refus fait avancer la fiche
prospect (gagné / perdu). « Payé / livré » reste réservé au Lot 5.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .db import CatalogOffer, JournalEntry, Prospect, Quote, QuoteLine, utcnow

STATUS_LABELS = {"brouillon": "Brouillon", "valide": "Validé", "envoye": "Envoyé",
                 "accepte": "Accepté", "refuse": "Refusé", "expire": "Expiré",
                 "annule": "Annulé"}
VALIDITY_DAYS = 30


class QuoteError(ValueError):
    pass


class QuoteService:
    def __init__(self, sessions: sessionmaker[Session],
                 set_stage: Callable[[int, str, str, str], None]) -> None:
        self._sessions = sessions
        self._set_stage = set_stage

    # ------------------------------------------------------------------ helpers
    def _quote(self, s: Session, quote_id: int) -> Quote:
        q = s.get(Quote, quote_id)
        if q is None:
            raise QuoteError(f"Devis {quote_id} introuvable")
        return q

    def _draft(self, s: Session, quote_id: int) -> Quote:
        q = self._quote(s, quote_id)
        if q.status != "brouillon":
            raise QuoteError("Seul un brouillon se modifie")
        return q

    def _retotal(self, s: Session, q: Quote) -> None:
        q.total = s.scalar(select(func.coalesce(func.sum(QuoteLine.total), 0)).where(
            QuoteLine.quote_id == q.id)) or 0
        q.updated_at = utcnow()

    def _journal(self, s: Session, q: Quote, action: str, text: str, by: str) -> None:
        s.add(JournalEntry(agent="humain", action_type=action, level=2, channel="interne",
                           pole=q.pole, status="executed", summary=f"{q.number} : {text}",
                           details={"devis": q.number, "total": q.total}, decided_by=by))

    # ------------------------------------------------------------------ saisie
    def create(self, prospect_id: int, by: str, notes: str = "") -> int:
        with self._sessions() as s:
            p = s.get(Prospect, prospect_id)
            if p is None:
                raise QuoteError(f"Prospect {prospect_id} introuvable")
            year = utcnow().year
            count = s.scalar(select(func.count()).select_from(Quote).where(
                Quote.number.like(f"DV-{year}-%"))) or 0
            q = Quote(number=f"DV-{year}-{count + 1:04d}", prospect_id=p.id, pole=p.pole,
                      notes=" ".join(notes.split())[:1000], created_by=by)
            s.add(q)
            s.flush()
            self._journal(s, q, "devis.cree", f"brouillon pour {p.email}", by)
            s.commit()
            return q.id

    def add_line(self, quote_id: int, by: str, offer_code: str = "", label: str = "",
                 price: int | None = None, qty: int = 1) -> None:
        if qty < 1 or qty > 10_000:
            raise QuoteError("Quantité : entre 1 et 10 000")
        with self._sessions() as s:
            q = self._draft(s, quote_id)
            if offer_code:
                o = s.scalar(select(CatalogOffer).where(CatalogOffer.code == offer_code))
                if o is None:
                    raise QuoteError(f"Offre inconnue : {offer_code}")
                if price is None:
                    if o.status != "actif" or o.price is None or len(o.prices or []) > 1:
                        raise QuoteError(f"« {o.name} » : tarif à confirmer ou sur devis. "
                                         "Saisissez le prix à la main.")
                    unit, source = int(o.price), "catalogue"
                else:
                    unit, source = price, "manuel"
                label = label or o.name
            else:
                if not label.strip() or price is None:
                    raise QuoteError("Une ligne libre demande un libellé et un prix")
                unit, source = price, "manuel"
            if unit < 0:
                raise QuoteError("Le prix ne peut pas être négatif")
            s.add(QuoteLine(quote_id=q.id, offer_code=offer_code, label=label.strip()[:300],
                            qty=qty, unit_price=unit, price_source=source, total=unit * qty))
            s.flush()
            self._retotal(s, q)
            s.commit()

    def remove_line(self, quote_id: int, line_id: int, by: str) -> None:
        with self._sessions() as s:
            q = self._draft(s, quote_id)
            line = s.get(QuoteLine, line_id)
            if line is None or line.quote_id != q.id:
                raise QuoteError("Ligne introuvable")
            s.delete(line)
            s.flush()
            self._retotal(s, q)
            s.commit()

    # ------------------------------------------------------------------ cycle de vie
    def validate(self, quote_id: int, by: str, days: int = VALIDITY_DAYS) -> None:
        with self._sessions() as s:
            q = self._draft(s, quote_id)
            lines = list(s.scalars(select(QuoteLine).where(QuoteLine.quote_id == q.id)).all())
            if not lines or q.total <= 0:
                raise QuoteError("Un devis validé comporte au moins une ligne payante")
            for ln in lines:
                if ln.price_source != "catalogue":
                    continue
                o = s.scalar(select(CatalogOffer).where(CatalogOffer.code == ln.offer_code))
                if o is None or o.status != "actif" or o.price != ln.unit_price:
                    raise QuoteError(f"Le tarif de « {ln.label} » a changé dans le catalogue : "
                                     "retirez la ligne et ajoutez-la de nouveau")
            now = utcnow()
            q.status, q.validated_by, q.validated_at = "valide", by, now
            q.valid_until = now + timedelta(days=max(days, 1))
            q.updated_at = now
            self._journal(s, q, "devis.valide", f"validé ({q.total} {q.currency})", by)
            s.commit()

    def mark_sent(self, quote_id: int, by: str) -> None:
        """Le devis validé a été remis au client (le message part par les canaux habituels)."""
        with self._sessions() as s:
            q = self._quote(s, quote_id)
            if q.status != "valide":
                raise QuoteError("Seul un devis validé peut être envoyé")
            q.status, q.sent_at, q.updated_at = "envoye", utcnow(), utcnow()
            self._journal(s, q, "devis.envoye", "envoyé au client", by)
            prospect_id, number = q.prospect_id, q.number
            s.commit()
        self._set_stage(prospect_id, "devis", by, f"Devis {number} envoyé")

    def decide(self, quote_id: int, accepted: bool, by: str) -> None:
        with self._sessions() as s:
            q = self._quote(s, quote_id)
            if q.status != "envoye":
                raise QuoteError("Seul un devis envoyé reçoit une réponse du client")
            q.status = "accepte" if accepted else "refuse"
            q.decided_at = q.updated_at = utcnow()
            self._journal(s, q, "devis.accepte" if accepted else "devis.refuse",
                          "accepté par le client" if accepted else "refusé par le client", by)
            prospect_id, number = q.prospect_id, q.number
            s.commit()
        self._set_stage(prospect_id, "gagne" if accepted else "perdu", by,
                        f"Devis {number} {'accepté' if accepted else 'refusé'}")

    def cancel(self, quote_id: int, by: str) -> None:
        with self._sessions() as s:
            q = self._quote(s, quote_id)
            if q.status not in ("brouillon", "valide", "envoye"):
                raise QuoteError("Ce devis est déjà clos")
            q.status, q.updated_at = "annule", utcnow()
            self._journal(s, q, "devis.annule", "annulé", by)
            s.commit()

    def expire_due(self, now: datetime | None = None) -> int:
        """Passe en « expiré » les devis validés ou envoyés dont la validité est dépassée."""
        now = now or utcnow()
        with self._sessions() as s:
            rows = s.scalars(select(Quote).where(
                Quote.status.in_(("valide", "envoye")), Quote.valid_until < now)).all()
            for q in rows:
                q.status, q.updated_at = "expire", now
                self._journal(s, q, "devis.expire", "validité dépassée", "système")
            s.commit()
            return len(rows)

    # ------------------------------------------------------------------ lecture
    def get(self, quote_id: int) -> tuple[Quote, list[QuoteLine]] | None:
        with self._sessions() as s:
            q = s.get(Quote, quote_id)
            if q is None:
                return None
            lines = list(s.scalars(select(QuoteLine).where(
                QuoteLine.quote_id == quote_id).order_by(QuoteLine.id)).all())
            return q, lines

    def listing(self, status: str = "", prospect_id: int | None = None,
                limit: int = 200) -> list[Quote]:
        with self._sessions() as s:
            qry = select(Quote)
            if status:
                qry = qry.where(Quote.status == status)
            if prospect_id is not None:
                qry = qry.where(Quote.prospect_id == prospect_id)
            return list(s.scalars(qry.order_by(Quote.id.desc()).limit(limit)).all())

    def pipeline(self) -> dict[str, dict[str, int]]:
        """Nombre et montant par statut, plus le taux de conversion des devis décidés."""
        with self._sessions() as s:
            rows = s.execute(select(Quote.status, func.count(), func.coalesce(
                func.sum(Quote.total), 0)).group_by(Quote.status)).all()
        out = {st: {"n": n, "total": int(t)} for st, n, t in rows}
        won, lost = out.get("accepte", {"n": 0})["n"], out.get("refuse", {"n": 0})["n"]
        out["_conversion"] = {"pct": round(100 * won / (won + lost)) if won + lost else 0}
        return out
