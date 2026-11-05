"""Licences et abonnements : qui a acheté quoi, jusqu'à quand, pour combien, et ce qui expire bientôt."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime

from sqlalchemy import select

from .db import License, utcnow

PLANS = {"mensuel": 1, "trimestriel": 3, "semestriel": 6, "annuel": 12,
         "unique": 0, "essai": 0, "gratuit": 0}                      # mois couverts par une période
BILLING = {"mensuel": "mensuel", "monthly": "mensuel", "trimestriel": "trimestriel", "quarterly": "trimestriel",
           "semestriel": "semestriel", "semiannual": "semestriel", "annuel": "annuel", "yearly": "annuel",
           "annual": "annuel"}
FREE_WORDS = {"free", "gratuit", "decouverte", "découverte"}
TRIAL_WORDS = {"trial", "essai"}
CANCEL_WORDS = {"cancelled", "canceled", "annule", "annulé", "resilie", "résilié", "resiliee"}
PAUSE_WORDS = {"suspended", "suspendu", "suspendue", "inactive", "inactif"}
STATUSES = ("active", "suspendue", "resiliee")
SOON_DAYS = 30


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _day(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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

    # ------------------------------------------------- import depuis les logiciels

    @staticmethod
    def normalize(raw: dict) -> dict | None:
        """Ligne brute d'un logiciel -> champs d'une licence ; None si démo ou inutilisable."""
        words = {str(raw.get(k) or "").strip().lower() for k in ("raw_status", "raw_plan", "kind", "etat")}
        if "demo" in words or not str(raw.get("client") or "").strip():
            return None
        starts = _day(raw.get("starts")) or datetime.now(UTC).date()
        ends = _day(raw.get("ends"))
        status_word = str(raw.get("raw_status") or "").strip().lower()
        if status_word in TRIAL_WORDS or raw.get("is_trial") in (1, "1", True):
            plan = "essai"
        elif words & FREE_WORDS or str(raw.get("raw_plan") or "").strip().lower() in FREE_WORDS:
            plan = "gratuit"
        else:
            plan = BILLING.get(str(raw.get("billing") or "").strip().lower(), "")
            if not plan:
                days = (ends - starts).days if ends else 0
                plan = "mensuel" if 0 < days <= 45 else ("annuel" if days else "unique")
        status = "resiliee" if status_word in CANCEL_WORDS else ("suspendue" if status_word in PAUSE_WORDS else "active")
        if status_word == "expired" and ends is None:
            ends = starts
        if plan == "gratuit":
            ends = None
        try:
            amount = max(0, int(float(raw.get("amount") or 0)))
        except (TypeError, ValueError):
            amount = 0
        return {"client": str(raw["client"]).strip()[:200], "contact": str(raw.get("contact") or "")[:200],
                "product": str(raw.get("product") or "")[:200], "plan": plan, "amount_fcfa": amount,
                "starts_on": starts, "ends_on": ends, "status": status, "site": str(raw.get("site") or "")[:200]}

    def import_rows(self, source: str, rows: list[dict]) -> dict:
        """Crée ou met à jour les licences d'un logiciel (clé : source + identifiant d'origine)."""
        created = updated = skipped = 0
        with self._sessions() as s:
            known = {r.external_id: r for r in s.scalars(select(License).where(License.source == source)).all()}
            for raw in rows:
                ext, data = str(raw.get("external_id") or ""), self.normalize(raw)
                if not ext or data is None:
                    skipped += 1
                    continue
                row = known.get(ext)
                if row is None:
                    s.add(License(source=source, external_id=ext, **data))
                    created += 1
                    continue
                changed = {k: v for k, v in data.items() if getattr(row, k) != v}
                if changed:
                    for k, v in changed.items():
                        setattr(row, k, v)
                    row.updated_at = utcnow()
                    updated += 1
            s.commit()
        return {"source": source, "created": created, "updated": updated, "skipped": skipped}

    def sync_file(self, path) -> list[dict]:
        """Lit le fichier écrit chaque nuit par le collecteur du serveur et importe tout."""
        import json
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            raise ValueError("Le collecteur n'a encore rien produit")
        data = json.loads(p.read_text(encoding="utf-8"))
        return [self.import_rows(src, rows) for src, rows in sorted(data.get("sources", {}).items())
                if isinstance(rows, list)]

    def rows(self, today: date | None = None) -> list[dict]:
        with self._sessions() as s:
            items = list(s.scalars(select(License).order_by(License.ends_on.is_(None), License.ends_on)).all())
        return [{"row": r, "state": state_of(r, today),
                 "days": (r.ends_on - (today or datetime.now(UTC).date())).days if r.ends_on else None} for r in items]

    def summary(self, today: date | None = None) -> dict:
        rows = self.rows(today)
        live = [r for r in rows if r["state"] in ("active", "bientot", "sans_echeance")]
        paid = [r for r in live if r["row"].plan not in ("gratuit", "essai")]
        monthly = 0.0
        for r in paid:
            months = PLANS[r["row"].plan]
            if months:
                monthly += r["row"].amount_fcfa / months
        return {"total": len(rows), "actives": len(paid),
                "gratuites": sum(r["row"].plan == "gratuit" for r in live),
                "essais": sum(r["row"].plan == "essai" and r["state"] != "expiree" for r in rows),
                "bientot": sum(r["state"] == "bientot" for r in rows),
                "expirees": sum(r["state"] == "expiree" for r in rows),
                "mensuel_fcfa": round(monthly)}
