"""Agent Veille et reporting : statistiques, e-réputation, tableau de bord (section 6).

Autonomie : lecture seule. Il mesure et alerte, il n'agit jamais sur un canal.

* Indicateurs de réussite (section 3), calculés sur une période et comparés aux cibles.
* Alertes, toutes les heures : pic de messages, mails mécontents (avis négatif), mails
  restés sans réponse au-delà de la cible (2 h ouvrées). Chaque élément n'est signalé
  qu'une fois.

Sources : les mails, le journal de l'agent et les commentaires des pages Facebook
raccordées (agents/commentaires.py).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import OrgConfig, Settings
from ..db import JournalEntry, PendingAction, ProcessedMessage, Prospect, User, utcnow
from ..governance import ActionRequest, Governor, as_utc

AGENT = "veille"
RESPONSE_CATEGORIES = ("prospect", "client", "support")
REPLY_ACTIONS = ("mail.faq_reply", "mail.reply", "support.answer", "whatsapp.reply",
                 "whatsapp.faq_reply", "whatsapp.support_answer")  # l'accusé de réception ne compte pas
NOT_CLASSIFIED = ("en_cours", "erreur", "a_trier_manuel")
SOCIAL_ACTIONS = ("social.post", "social.manual_post")
# Temps de travail manuel évité par action réalisée (minutes, estimation prudente à
# ajuster) : lire et classer un mail, rédiger une réponse, une publication, un article…
TIME_SAVED_MINUTES = {
    "mail.label": 1, "mail.ack": 2, "mail.faq_reply": 5, "support.answer": 8,
    "sara.answer": 5, "whatsapp.ack": 1, "whatsapp.faq_reply": 4,
    "whatsapp.support_answer": 6, "whatsapp.reply": 4, "mail.reply": 6,
    "mail.forward_internal": 2, "commercial.followup": 6, "social.post": 25,
    "social.manual_post": 20, "social.schedule_approved": 5, "web.article_draft": 120,
    "campaign.mail": 60, "report.publish": 15,
}
REVIEW_MINUTES = 1.5  # temps humain de relecture d'un élément validé
BASELINE_SETTING = "heures_manuelles_semaine"


# ------------------------------------------------------------------ heures ouvrées
def business_hours_between(start: datetime, end: datetime, tz: str, open_hour: int,
                           close_hour: int, workdays: tuple[int, ...] = (0, 1, 2, 3, 4)
                           ) -> float:
    """Heures ouvrées écoulées entre deux instants (lundi–vendredi par défaut)."""
    zone = ZoneInfo(tz)
    start, end = as_utc(start).astimezone(zone), as_utc(end).astimezone(zone)
    if end <= start:
        return 0.0
    total = 0.0
    day = start.date()
    while day <= end.date():
        if day.weekday() in workdays:
            opening = datetime.combine(day, time(open_hour), zone)
            closing = datetime.combine(day, time(close_hour), zone)
            lo, hi = max(start, opening), min(end, closing)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return total


# ------------------------------------------------------------------ indicateurs
@dataclass
class Indicator:
    nom: str
    valeur: str
    cible: str
    etat: str  # ok | alerte | a_mesurer
    detail: str = ""


@dataclass
class Indicators:
    debut: datetime
    fin: datetime
    items: list[Indicator] = field(default_factory=list)

    def as_text(self) -> str:
        mark = {"ok": "✔", "alerte": "✖", "a_mesurer": "…"}
        lines = [f"Indicateurs du {self.debut:%d/%m/%Y} au {self.fin:%d/%m/%Y}", ""]
        for i in self.items:
            lines.append(f"{mark[i.etat]} {i.nom} : {i.valeur} (cible : {i.cible})"
                         + (f" — {i.detail}" if i.detail else ""))
        return "\n".join(lines)


@dataclass
class AlertResult:
    pic: bool = False
    negatifs: int = 0
    sans_reponse: int = 0
    envoyees: int = 0


class VeilleAgent:
    def __init__(self, settings: Settings, org: OrgConfig, governor: Governor,
                 session_factory: sessionmaker[Session], connected_mailboxes: set[str]) -> None:
        self.settings = settings
        self.org = org
        self.gov = governor
        self._sessions = session_factory
        self.connected_mailboxes = connected_mailboxes
        self._sent = 0

    def _bh(self, start: datetime, end: datetime) -> float:
        return business_hours_between(start, end, self.settings.timezone,
                                      self.settings.business_open_hour,
                                      self.settings.business_close_hour)

    def _first_replies(self, since: datetime) -> dict[str, datetime]:
        """Première réponse réelle (FAQ ou réponse validée) envoyée pour chaque mail."""
        with self._sessions() as s:
            rows = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type.in_(REPLY_ACTIONS),
                JournalEntry.status == "executed",
                JournalEntry.created_at >= since,
            )).all()
        first: dict[str, datetime] = {}
        for r in rows:
            ref = (r.details or {}).get("ref")
            if ref and (ref not in first or as_utc(r.created_at) < first[ref]):
                first[ref] = as_utc(r.created_at)
        return first

    # -------------------------------------------------------------- indicateurs
    def indicators(self, days: int = 7, now: datetime | None = None) -> Indicators:
        now = now or utcnow()
        start = now - timedelta(days=days)
        out = Indicators(start, now)
        target_h = self.settings.response_target_hours
        with self._sessions() as s:
            mails = s.scalars(select(ProcessedMessage).where(
                ProcessedMessage.processed_at >= start)).all()
            journal = s.scalars(select(JournalEntry).where(
                JournalEntry.created_at >= start)).all()
            executed = s.scalars(select(PendingAction).where(
                PendingAction.status == "executed", PendingAction.decided_at >= start)).all()
            prospects = s.scalars(select(Prospect).where(Prospect.created_at >= start)).all()

        # 1. Ne rien laisser passer : mails lus et classés
        classified = [m for m in mails if m.decision not in NOT_CLASSIFIED]
        pct = 100 * len(classified) / len(mails) if mails else None
        out.items.append(Indicator(
            "Mails lus et classés", f"{pct:.0f} % ({len(classified)}/{len(mails)})"
            if pct is not None else "aucun mail", "100 %",
            "a_mesurer" if pct is None else ("ok" if pct == 100 else "alerte")))

        # 2. Répondre vite : délai de première réponse en heures ouvrées
        to_answer = [m for m in mails if m.category in RESPONSE_CATEGORIES and not m.suspicious]
        replies = self._first_replies(start - timedelta(days=1))
        delays, waiting = [], 0
        for m in to_answer:
            received = as_utc(m.received_at or m.processed_at)
            if m.message_id in replies:
                delays.append(self._bh(received, replies[m.message_id]))
            else:
                waiting += 1
        if delays:
            delays.sort()
            median = delays[len(delays) // 2]
            within = 100 * sum(d <= target_h for d in delays) / len(delays)
            out.items.append(Indicator(
                "Délai de première réponse (médiane, heures ouvrées)", f"{median:.1f} h",
                f"moins de {target_h:g} h", "ok" if median <= target_h else "alerte",
                f"{within:.0f} % des réponses dans la cible, {waiting} mail(s) sans réponse "
                "(hors accusés de réception)"))
        else:
            out.items.append(Indicator(
                "Délai de première réponse (médiane, heures ouvrées)", "—",
                f"moins de {target_h:g} h", "a_mesurer",
                f"{waiting} mail(s) à traiter sans réponse" if waiting else "aucune réponse"))

        # 3. Publier régulièrement : publications par pôle (ramenées à la semaine)
        weeks = max(days / 7, 1)
        per_pole = Counter(e.pole for e in journal
                           if e.action_type in SOCIAL_ACTIONS and e.status == "executed")
        poles = [p.code for p in self.org.poles if p.code != "GROUPE"]
        late = [p for p in poles if per_pole.get(p, 0) / weeks < 3]
        out.items.append(Indicator(
            "Publications par pôle et par semaine",
            ", ".join(f"{p} {per_pole.get(p, 0) / weeks:.1f}" for p in poles),
            "3 minimum", "ok" if not late else "alerte",
            f"sous la cible : {', '.join(late)}" if late else ""))

        # 4. Convertir : prospects transmis aux commerciaux
        by_pole = Counter(p.pole for p in prospects)
        out.items.append(Indicator(
            "Prospects qualifiés transmis", str(len(prospects)), "à fixer avec les pôles",
            "a_mesurer", ", ".join(f"{k} {v}" for k, v in sorted(by_pole.items()))))

        # 5. Rester fiable : contenus validés malgré une alerte de la base de connaissances
        risky = [pa for pa in executed if pa.payload.get("alertes")]
        out.items.append(Indicator(
            "Contenus envoyés malgré une alerte factuelle", str(len(risky)), "zéro",
            "ok" if not risky else "alerte",
            "; ".join(pa.title[:60] for pa in risky[:5])))

        # 6. Garder la main : action de niveau 2 ou 3 exécutée sans décision humaine
        unchecked = [e for e in journal if e.level >= 2 and e.status == "executed"
                     and not e.decided_by]
        out.items.append(Indicator(
            "Actions sensibles exécutées sans validation", str(len(unchecked)), "zéro",
            "ok" if not unchecked else "alerte"))

        # 7. Qualité des brouillons (R-08) : validés sans modification
        replies_done = [pa for pa in executed if pa.action_type == "mail.reply"]
        if replies_done:
            clean = sum(not pa.payload.get("modifie_par_valideur") for pa in replies_done)
            pct = 100 * clean / len(replies_done)
            out.items.append(Indicator(
                "Brouillons de réponse validés sans modification", f"{pct:.0f} %", "80 %",
                "ok" if pct >= 80 else "alerte", f"{len(replies_done)} brouillon(s) validé(s)"))
        else:
            out.items.append(Indicator("Brouillons de réponse validés sans modification", "—",
                                       "80 %", "a_mesurer"))
        out.items.insert(0, self._time_saved(journal, executed, weeks))
        return out

    def _time_saved(self, journal: list[JournalEntry], executed: list[PendingAction],
                    weeks: float) -> Indicator:
        """Soulager l'équipe (section 3) : heures de travail manuel évitées, nettes du
        temps de relecture, comparées au temps passé avant l'agent s'il est renseigné."""
        from ..configstore import service_value

        done = [e for e in journal if e.status == "executed"
                and e.action_type in TIME_SAVED_MINUTES]
        minutes = sum(TIME_SAVED_MINUTES[e.action_type] for e in done)
        minutes -= REVIEW_MINUTES * len(executed)
        hours = max(minutes, 0) / 60
        per_week = hours / weeks
        try:
            baseline = float(service_value(self._sessions, self.settings,
                                           BASELINE_SETTING).replace(",", ".") or 0)
        except ValueError:
            baseline = 0.0
        name = "Heures de travail manuel économisées"
        detail = (f"{per_week:.1f} h par semaine · {len(done)} action(s) de l'agent, "
                  f"relecture de {len(executed)} validation(s) déduite")
        if baseline <= 0:
            return Indicator(name, f"{hours:.1f} h", "−60 % (à mesurer)", "a_mesurer",
                             detail + " · indiquez le temps passé avant l'agent pour "
                                      "calculer le pourcentage")
        pct = 100 * per_week / baseline
        return Indicator(name, f"−{pct:.0f} % ({hours:.1f} h)", "−60 %",
                         "ok" if pct >= 60 else "alerte",
                         detail + f" · référence : {baseline:g} h par semaine avant l'agent")

    def weekly_report(self, now: datetime | None = None) -> Indicators:
        report = self.indicators(7, now)
        self.gov.submit(ActionRequest(
            agent=AGENT, action_type="report.publish", channel="rapport",
            title=f"Indicateurs de la semaine au {report.fin:%d/%m/%Y}",
            payload={"text": report.as_text()},
        ))
        self._send(self._direction(), "[IBIG] Indicateurs de la semaine", report.as_text())
        return report

    # -------------------------------------------------------------- alertes
    def _already_alerted(self, since: datetime) -> set[str]:
        """Clés « type:référence » déjà signalées : une alerte par type et par élément."""
        with self._sessions() as s:
            rows = s.scalars(select(JournalEntry).where(
                JournalEntry.action_type == "veille.alert",
                JournalEntry.created_at >= since)).all()
        return {f"{(r.details or {}).get('kind')}:{ref}"
                for r in rows for ref in (r.details or {}).get("refs", [])}

    def check_alerts(self, now: datetime | None = None) -> AlertResult:
        now = now or utcnow()
        result = AlertResult()
        hour_ago, week_ago = now - timedelta(hours=1), now - timedelta(days=7)
        with self._sessions() as s:
            recent = s.scalars(select(ProcessedMessage).where(
                ProcessedMessage.processed_at >= week_ago)).all()
        done = self._already_alerted(week_ago)

        # Pic de messages : dernière heure comparée à la moyenne horaire de la semaine
        last_hour = [m for m in recent if as_utc(m.processed_at) >= hour_ago]
        before = [m for m in recent if as_utc(m.processed_at) < hour_ago]
        hourly = len(before) / (7 * 24)
        spike_ref = f"{now:%Y-%m-%d-%H}"
        if (len(last_hour) >= self.settings.spike_min_messages
                and len(last_hour) >= self.settings.spike_factor * max(hourly, 0.1)
                and f"pic:{spike_ref}" not in done):
            result.pic = True
            per_pole = Counter(m.pole or "—" for m in last_hour)
            self._alert("pic", [spike_ref], self._direction(),
                        f"[IBIG] Pic de messages : {len(last_hour)} mails en une heure",
                        f"{len(last_hour)} mails reçus dans la dernière heure (moyenne : "
                        f"{hourly:.1f} par heure sur 7 jours).\nPar pôle : "
                        + ", ".join(f"{k} {v}" for k, v in per_pole.most_common()))

        # Avis négatifs : mails mécontents pas encore signalés
        negative = [m for m in recent if m.sentiment == "negatif" and not m.suspicious
                    and m.category != "spam" and f"negatif:{m.message_id}" not in done]
        if negative:
            result.negatifs = len(negative)
            self._alert_by_pole("negatif", negative,
                                "[IBIG] {n} message(s) mécontent(s) à suivre",
                                "Messages au ton négatif (réclamation, insatisfaction) :")

        # Sans réponse au-delà de la cible
        target = self.settings.response_target_hours
        replies = self._first_replies(week_ago - timedelta(days=1))
        overdue = [m for m in recent
                   if m.category in RESPONSE_CATEGORIES and not m.suspicious
                   and m.message_id not in replies and f"sans_reponse:{m.message_id}" not in done
                   and self._bh(as_utc(m.received_at or m.processed_at), now) > target]
        if overdue:
            result.sans_reponse = len(overdue)
            self._alert_by_pole("sans_reponse", overdue,
                                "[IBIG] {n} mail(s) sans réponse depuis plus de "
                                f"{target:g} h ouvrées",
                                "Ces mails attendent une réponse :")
        result.envoyees = self._sent
        return result

    # -------------------------------------------------------------- envoi
    def notify_direction(self, subject: str, body: str) -> None:
        """Mail à la direction et à l'administration (si une boîte d'envoi est réglée)."""
        self._send(self._direction(), subject, body)

    def _direction(self) -> list[str]:
        with self._sessions() as s:
            return list(s.scalars(select(User.email).where(
                User.role.in_(("direction", "admin")), User.active.is_(True))).all())

    def _alert_by_pole(self, kind: str, mails: list[ProcessedMessage], subject: str,
                       intro: str) -> None:
        by_pole: dict[str, list[ProcessedMessage]] = defaultdict(list)
        for m in mails:
            by_pole[m.pole or ""].append(m)
        for pole_code, items in by_pole.items():
            pole = self.org.pole(pole_code)
            recipients = {*self._direction(), *([pole.valideur] if pole and pole.valideur
                                                else [])}
            lines = [intro, ""] + [
                f"- [{pole_code or '—'}] {m.subject} — {m.sender} "
                f"({m.summary or m.category}), boîte {m.mailbox}" for m in items]
            self._alert(kind, [m.message_id for m in items], sorted(recipients),
                        subject.format(n=len(items)), "\n".join(lines), pole_code)

    def _alert(self, kind: str, refs: list[str], recipients: list[str], subject: str,
               body: str, pole: str = "") -> None:
        # Toujours visible au journal (et donc au tableau de bord), même sans boîte d'envoi.
        with self._sessions() as s:
            s.add(JournalEntry(agent=AGENT, action_type="veille.alert", level=1,
                               channel="veille", pole=pole, status="flagged",
                               summary=subject, details={"kind": kind, "refs": refs,
                                                         "texte": body}))
            s.commit()
        self._send(recipients, subject, body)

    def _send(self, recipients: list[str], subject: str, body: str) -> None:
        mailbox = self.settings.notification_mailbox
        if not mailbox or mailbox not in self.connected_mailboxes:
            return
        url = self.settings.dashboard_url.rstrip("/")
        for to in recipients:
            out = self.gov.submit(ActionRequest(
                agent=AGENT, action_type="notify.internal", channel="interne",
                account=mailbox, title=f"Veille → {to} : {subject}",
                payload={"mailbox": mailbox, "to": to, "subject": subject,
                         "body": f"Bonjour,\n\n{body}\n\nTableau de bord : {url}\n\n"
                                 "Message envoyé automatiquement par l'agent IA IBIG."},
            ))
            if out.status == "executed":
                self._sent += 1
