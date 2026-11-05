"""« Ma journée » : une seule page pour la direction, ouverte le matin.

Elle ne calcule rien de nouveau : elle rassemble ce que l'agent sait déjà (écosystème, validations,
consignes, ventes, argent, support, sauvegardes, IBIG CORE) et en tire les trois choses à faire d'abord.
Lecture seule : aucune action n'est lancée d'ici.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from ..auth import Principal
from ..db import JournalEntry, PendingAction, utcnow
from ..llm import effective_budget
from .stats import home_stats, sales_summary

MAX_DECISIONS = 3
SENT_KEYS = ("body", "texte", "contenu_html")


def _decisions(rt, who: Principal) -> tuple[list[dict], int]:
    """Les (au plus 3) propositions les plus anciennes que cette personne peut trancher, avec
    le texte déjà prêt. Les autres attendent : on n'en montre jamais plus de trois à la fois."""
    with rt.sessions() as s:
        rows = s.scalars(select(PendingAction).where(PendingAction.status == "pending")
                         .order_by(PendingAction.created_at)).all()
    mine = [pa for pa in rows if who.can_decide(pa)]
    cards = []
    for pa in mine[:MAX_DECISIONS]:
        text = next((pa.payload[k] for k in SENT_KEYS if isinstance(pa.payload.get(k), str)), "")
        cards.append({"id": pa.id, "titre": pa.title, "pole": pa.pole, "type": pa.action_type,
                      "texte": text[:600], "resume": pa.payload.get("resume", ""),
                      "incomplet": "COMPLÉTER" in text.upper()})
    return cards, len(mine)


def _handled_alone(rt) -> int:
    """Ce que les agents ont fait seuls depuis 24 h (niveau automatique), pour montrer le travail épargné."""
    since = utcnow() - timedelta(hours=24)
    with rt.sessions() as s:
        return s.scalar(select(func.count()).select_from(JournalEntry).where(
            JournalEntry.level == 1, JournalEntry.status == "executed",
            JournalEntry.created_at >= since)) or 0

TONE_BY_LEVEL = {"ok": "ok", "attention": "warn", "critique": "bad", "inconnu": "info"}
BACKUP_MAX_H = 36


def _priorities(c: dict) -> list[dict]:
    """Les choses à faire d'abord, de la plus urgente à la moins urgente (trois au plus)."""
    items: list[dict] = []
    for i in c["urgent_incidents"][:2]:
        items.append({"texte": i.title, "lien": "/ecosysteme", "ton": "bad"})
    if c["pending"]:
        items.append({"texte": f"{c['pending']} action(s) attendent votre validation", "lien": "/validations",
                      "ton": "warn"})
    if c["consignes_a_preciser"]:
        items.append({"texte": f"{c['consignes_a_preciser']} consigne(s) attendent votre précision",
                      "lien": "/consigne", "ton": "warn"})
    if c["payments_to_confirm"]:
        items.append({"texte": f"{c['payments_to_confirm']} paiement(s) à confirmer", "lien": "/finance",
                      "ton": "warn"})
    if c["tranches_retard"]:
        items.append({"texte": f"{c['tranches_retard']} tranche(s) de paiement EDUFORM en retard : relancer",
                      "lien": "/ecosysteme", "ton": "warn"})
    if c["licences_a_renouveler"]:
        items.append({"texte": f"{c['licences_a_renouveler']} licence(s) payante(s) arrivent à échéance : "
                               "relancer le renouvellement", "lien": "/licences?filtre=bientot", "ton": "warn"})
    if c["licences_expirees"]:
        items.append({"texte": f"{c['licences_expirees']} licence(s) payante(s) expirée(s) récemment",
                      "lien": "/licences?filtre=expiree", "ton": "warn"})
    if c["quotes_waiting"]:
        items.append({"texte": f"{c['quotes_waiting']} devis attendent la réponse du client", "lien": "/devis",
                      "ton": "info"})
    if c["hot_prospects"]:
        items.append({"texte": f"{c['hot_prospects']} prospect(s) chaud(s) à rappeler", "lien": "/prospects",
                      "ton": "info"})
    if c["open_tickets"]:
        items.append({"texte": f"{c['open_tickets']} demande(s) de support ouvertes", "lien": "/tickets",
                      "ton": "info"})
    return items[:3]


def build(rt, who: Principal) -> dict:
    eco = rt.ecosystem
    summary = eco.summary()
    incidents = [i for i in eco.incidents() if i.severity != "info" and i.status == "open"]
    with rt.sessions() as s:
        stats = home_stats(s, who, effective_budget(rt.sessions, rt.settings))
    sales = sales_summary(rt)
    entries = rt.consigne.entries()
    server = eco.server_state()
    ages = [b["age_h"] for b in (server or {}).get("backups", []) if b.get("age_h") is not None]
    missing = [b["name"] for b in (server or {}).get("backups", []) if b.get("age_h") is None]
    backups_ok = bool(server) and not missing and bool(ages) and max(ages) <= BACKUP_MAX_H
    core = [i for i in incidents if i.key.startswith("anom|core|")]
    tranches = next((int(i.title.rsplit("(", 1)[1].rstrip(")")) for i in core if i.key == "anom|core|5"
                     and i.title.endswith(")") and i.title.rsplit("(", 1)[1].rstrip(")").isdigit()), 0)
    paid = [r for r in rt.licences.rows() if r["row"].plan not in ("gratuit", "essai")]
    ctx = {
        "licences_a_renouveler": sum(r["state"] == "bientot" for r in paid),
        "licences_expirees": sum(r["state"] == "expiree" and r["days"] is not None and r["days"] >= -30
                                 for r in paid),
        "urgent_incidents": [i for i in incidents if i.severity == "critical"],
        "pending": stats.pending, "payments_to_confirm": sales["payments_to_confirm"],
        "consignes_a_preciser": sum(e.status == "a_preciser" for e in entries),
        "tranches_retard": tranches, "quotes_waiting": sales["quotes_waiting"],
        "hot_prospects": stats.hot_prospects, "open_tickets": stats.open_tickets,
    }
    decisions, waiting = _decisions(rt, who)
    return {
        "decisions": decisions, "decisions_total": waiting, "handled_alone": _handled_alone(rt),
        "niveau": summary["level"], "ton": TONE_BY_LEVEL[summary["level"]], "message": summary["message"],
        "suivis": summary["totals"]["suivis"], "dispo": summary["totals"]["disponibilite"],
        "priorites": _priorities(ctx),
        "incidents": incidents[:6], "nb_incidents": len(incidents),
        "pending": stats.pending, "prepared": stats.prepared,
        "outstanding": sales["outstanding"], "payments_to_confirm": sales["payments_to_confirm"],
        "quotes_waiting": sales["quotes_waiting"], "quotes_draft": sales["quotes_draft"],
        "missions_running": sales["missions_running"],
        "prospects": stats.prospects, "hot_prospects": stats.hot_prospects, "open_tickets": stats.open_tickets,
        "mails_24h": stats.mails_24h,
        "core_findings": len(core), "tranches_retard": tranches,
        "backups_ok": backups_ok, "backups_known": bool(server),
        "backups_oldest_h": round(max(ages)) if ages else None,
        "consignes": list(reversed(entries))[:3],
        "ai_spend": round(stats.ai_spend, 2), "ai_budget": stats.ai_budget,
    }
