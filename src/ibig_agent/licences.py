"""Licences et abonnements : qui a acheté quoi, jusqu'à quand, pour combien, et ce qui expire bientôt."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime

from sqlalchemy import select

from .db import License, utcnow

PLANS = {"mensuel": 1, "annuel": 12, "unique": 0, "essai": 0}      # mois couverts par une période
STATUSES = ("active", "suspendue", "resiliee")
SOON_DAYS = 30


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def state_of(row: License, today: date | None = None) -> str:
    """active | bientot | expiree | suspendue | resiliee | sans_echeance"""
    today = today or datetime.now(UTC).date()
    if row.status != "active":
        return row.status
    if row.ends_on is None:
        return "sans_echeance"
    if row.ends_on < today:
        return "expiree"
    return "bientot" if (row.ends_on - today).days <= SOON_DAYS else "active"


class LicenseService:
    def __init__(self, sessions):
        self._sessions = sessions

    def add(self, client: str, product: str, plan: str, amount_fcfa: int, starts_on: date,
            ends_on: date | None = None, contact: str = "", site: str = "", notes: str = "") -> int:
        client, product = client.strip(), product.strip()
        if not client or not product:
            raise ValueError("Le client et le produit sont obligatoires")
        if plan not in PLANS:
            raise ValueError("Formule inconnue")
        if amount_fcfa < 0:
            raise ValueError("Le montant ne peut pas être négatif")
        if ends_on is None and PLANS[plan]:
            ends_on = add_months(starts_on, PLANS[plan])
        if ends_on is not None and ends_on < starts_on:
            raise ValueError("La fin ne peut pas précéder le début")
        with self._sessions() as s:
            row = License(client=client[:200], contact=contact.strip()[:200], product=product[:200], plan=plan,
                          amount_fcfa=amount_fcfa, starts_on=starts_on, ends_on=ends_on,
                          site=site.strip()[:200], notes=notes.strip())
            s.add(row)
            s.commit()
            return row.id

    def renew(self, license_id: int, today: date | None = None) -> date:
        """Prolonge d'une période à partir de l'échéance (ou d'aujourd'hui si déjà expirée) et réactive."""
        today = today or datetime.now(UTC).date()
        with self._sessions() as s:
            row = s.get(License, license_id)
            if row is None:
                raise ValueError("Licence introuvable")
            months = PLANS[row.plan]
            if not months:
                raise ValueError("Cette formule n'a pas de période à renouveler")
            base = max(row.ends_on or today, today)
            row.ends_on = add_months(base, months)
            row.status = "active"
            row.updated_at = utcnow()
            s.commit()
            return row.ends_on

    def set_status(self, license_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("Statut inconnu")
        with self._sessions() as s:
            row = s.get(License, license_id)
            if row is None:
                raise ValueError("Licence introuvable")
            row.status = status
            row.updated_at = utcnow()
            s.commit()

    def rows(self, today: date | None = None) -> list[dict]:
        with self._sessions() as s:
            items = list(s.scalars(select(License).order_by(License.ends_on.is_(None), License.ends_on)).all())
        return [{"row": r, "state": state_of(r, today),
                 "days": (r.ends_on - (today or datetime.now(UTC).date())).days if r.ends_on else None} for r in items]

    def summary(self, today: date | None = None) -> dict:
        rows = self.rows(today)
        live = [r for r in rows if r["state"] in ("active", "bientot", "sans_echeance")]
        monthly = 0.0
        for r in live:
            months = PLANS[r["row"].plan]
            if months:
                monthly += r["row"].amount_fcfa / months
        return {"total": len(rows), "actives": len(live),
                "bientot": sum(r["state"] == "bientot" for r in rows),
                "expirees": sum(r["state"] == "expiree" for r in rows),
                "mensuel_fcfa": round(monthly)}
