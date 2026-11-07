"""Registre du catalogue commercial (cahier des charges, section 8).

Chaque offre porte un identifiant stable, une version, un statut, une date de mise à jour et un
responsable de validation, avec l'historique de ses tarifs. L'import (catalog_import) met le
registre à jour ; les fichiers que lit l'agent en sont générés.

Règles :
* une offre dont un humain a décidé (statut, tarif) n'est plus jamais modifiée silencieusement
  par l'import : si son tarif change à la source, elle repasse « à confirmer » ;
* une offre absente d'un import COMPLET est archivée, jamais supprimée (historique conservé) ;
* un tarif ambigu (plusieurs prix) ou absent n'est jamais « actif » : l'agent ne l'annonce pas.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .catalog_import import BRANCHES, HIDDEN, SITE_EDUFORM, Group, Item, source_for
from .db import CatalogOffer, CatalogPriceHistory, utcnow

STATUSES = ("actif", "a_confirmer", "sur_devis", "suspendu", "indisponible", "archive")
LABELS = {"actif": "Actif", "a_confirmer": "À confirmer", "sur_devis": "Sur devis",
          "suspendu": "Suspendu", "indisponible": "Indisponible", "archive": "Archivé"}
IMPORT_BY = "Import IBIG PARTNERS"
IMPORT_NOTE = f"{IMPORT_BY} — tarifs publics confirmés par la Direction (30/10/2026)"
IMPORT_ACTIVE = ("actif", "a_confirmer", "sur_devis")   # statuts que l'import peut archiver


def default_status(prices: list[int]) -> str:
    return "actif" if len(prices) == 1 else "a_confirmer"


def managed_by_import(o: CatalogOffer) -> bool:
    """Vrai tant qu'aucun humain n'a décidé de cette offre."""
    return not o.validated_by or o.validated_by.startswith(IMPORT_BY)


def _history(s: Session, o: CatalogOffer, by: str, reason: str, now: datetime) -> None:
    s.add(CatalogPriceHistory(offer_id=o.id, price=o.price, prices=list(o.prices or []),
                              changed_at=now, changed_by=by, reason=reason))


def sync(sessions: sessionmaker[Session], groups: list[Group], complete: set[str],
         now: datetime | None = None) -> dict[str, int]:
    """Met le registre à jour d'après un import. `complete` : marques lues en entier (seules
    leurs offres absentes peuvent être archivées)."""
    now = now or utcnow()
    stats = {"crees": 0, "tarifs_modifies": 0, "mises_a_jour": 0, "archives": 0,
             "reactives": 0, "inchanges": 0}
    with sessions() as s:
        existing = {o.code: o for o in s.scalars(select(CatalogOffer)).all()}
        seen: set[str] = set()
        for g in groups:
            for it in g.items:
                seen.add(it.code)
                prices = sorted(it.prices)
                url = "\n".join(it.urls)
                source = SITE_EDUFORM if it.origin == "site" else source_for(g.slug)
                o = existing.get(it.code)
                if o is None:
                    status = default_status(prices)
                    o = CatalogOffer(
                        code=it.code, brand=g.brand, pole=g.pole, name=it.name, kind=it.kind,
                        category=it.category, price=prices[0] if len(prices) == 1 else None,
                        prices=prices, source_prices=prices, url=url,
                        description=it.description, source=source, status=status, version=1,
                        validated_by=IMPORT_NOTE if status == "actif" else "",
                        validated_at=now if status == "actif" else None,
                        created_at=now, updated_at=now)
                    s.add(o)
                    s.flush()
                    _history(s, o, IMPORT_BY, "création", now)
                    stats["crees"] += 1
                    continue
                changed = False
                # Ce qui compte, c'est un changement À LA SOURCE : un tarif fixé par un humain
                # n'est pas défait parce que la source reste ambiguë ou inchangée.
                if sorted(o.source_prices or []) != prices:
                    human = not managed_by_import(o)
                    o.source_prices = prices
                    o.version += 1
                    if human:   # tarif décidé par un humain : on le garde, à revalider
                        if o.status not in HIDDEN:
                            o.status = "a_confirmer"
                        reason = "tarif modifié à la source : à revalider"
                    else:
                        o.prices = prices
                        o.price = prices[0] if len(prices) == 1 else None
                        if len(prices) != 1:
                            o.status = "a_confirmer" if o.status not in HIDDEN else o.status
                        elif o.status in ("a_confirmer", "actif"):
                            o.status = "actif"
                        reason = "changement de tarif à l'import"
                    _history(s, o, IMPORT_BY, reason, now)
                    stats["tarifs_modifies"] += 1
                    changed = True
                if o.status == "archive" and managed_by_import(o):
                    o.status = default_status(prices)
                    _history(s, o, IMPORT_BY, "réapparue à l'import", now)
                    stats["reactives"] += 1
                    changed = True
                for attr, value in (("brand", g.brand), ("pole", g.pole), ("name", it.name),
                                    ("kind", it.kind), ("category", it.category), ("url", url),
                                    ("description", it.description), ("source", source)):
                    if getattr(o, attr) != value:
                        setattr(o, attr, value)
                        changed = True
                if changed:
                    o.updated_at = now
                    stats["mises_a_jour"] += 1
                else:
                    stats["inchanges"] += 1
        for code, o in existing.items():
            if (code not in seen and o.brand in complete and managed_by_import(o)
                    and o.status in IMPORT_ACTIVE):
                o.status = "archive"
                o.updated_at = now
                _history(s, o, IMPORT_BY, "absente de l'import complet", now)
                stats["archives"] += 1
        s.commit()
    return stats


def groups_from_db(sessions: sessionmaker[Session]) -> list[Group]:
    """Les offres du registre, regroupées par marque dans l'ordre de BRANCHES (pour générer les
    fichiers de l'agent). Les offres masquées sont incluses : render() les écarte."""
    with sessions() as s:
        rows = s.scalars(select(CatalogOffer).order_by(CatalogOffer.name)).all()
    by_slug: dict[str, list[CatalogOffer]] = {}
    for o in rows:
        by_slug.setdefault(o.code.split(":", 1)[0], []).append(o)
    groups = []
    for slug, pole, brand in BRANCHES:
        offers = by_slug.get(slug, [])
        items = [Item(name=o.name, kind=o.kind, prices=list(o.prices or []),
                      urls=[u for u in (o.url or "").splitlines() if u],
                      description=o.description, category=o.category,
                      origin="site" if (o.source or "").startswith(SITE_EDUFORM) else "partners",
                      code=o.code, status=o.status) for o in offers]
        if items:
            groups.append(Group(slug, pole, brand, items))
    return groups


# ------------------------------------------------------------------ décisions humaines
def set_status(sessions: sessionmaker[Session], offer_id: int, status: str, by: str,
               now: datetime | None = None) -> tuple[bool, str]:
    """Change le statut d'une offre. « Actif » exige un tarif unique : sinon fixer le tarif."""
    if status not in STATUSES:
        return False, f"statut inconnu : {status}"
    now = now or utcnow()
    with sessions() as s:
        o = s.get(CatalogOffer, offer_id)
        if o is None:
            return False, "offre introuvable"
        if status == "actif" and len(o.prices or []) != 1:
            return False, ("« Actif » exige un tarif unique : fixez d'abord le tarif "
                           "(plusieurs tarifs ou aucun au catalogue)")
        o.status = status
        o.validated_by, o.validated_at, o.updated_at = by, now, now
        o.version += 1
        _history(s, o, by, f"statut : {LABELS[status]}", now)
        s.commit()
    return True, f"Statut « {LABELS[status]} » enregistré"


def set_price(sessions: sessionmaker[Session], offer_id: int, price: int, by: str,
              now: datetime | None = None) -> tuple[bool, str]:
    """Fixe le tarif officiel d'une offre (cas d'un tarif ambigu) et la valide."""
    if price <= 0:
        return False, "le tarif doit être un montant positif"
    now = now or utcnow()
    with sessions() as s:
        o = s.get(CatalogOffer, offer_id)
        if o is None:
            return False, "offre introuvable"
        o.prices, o.price, o.status = [price], price, "actif"
        o.validated_by, o.validated_at, o.updated_at = by, now, now
        o.version += 1
        _history(s, o, by, "tarif fixé et validé", now)
        s.commit()
    return True, "Tarif fixé et offre validée"


def list_offers(sessions: sessionmaker[Session], q: str = "", pole: str = "",
                status: str = "", limit: int = 200) -> tuple[list[CatalogOffer], int]:
    """Offres filtrées (recherche dans le nom), et nombre total avant la limite."""
    with sessions() as s:
        query = select(CatalogOffer)
        if q.strip():
            query = query.where(CatalogOffer.name.ilike(f"%{q.strip()}%"))
        if pole:
            query = query.where(CatalogOffer.pole == pole)
        if status:
            query = query.where(CatalogOffer.status == status)
        total = s.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = s.scalars(query.order_by(CatalogOffer.pole, CatalogOffer.name).limit(limit)).all()
    return list(rows), int(total)


def status_counts(sessions: sessionmaker[Session]) -> dict[str, int]:
    with sessions() as s:
        rows = s.execute(select(CatalogOffer.status, func.count()).group_by(
            CatalogOffer.status)).all()
    return {status: n for status, n in rows}
