"""Missions confiées aux agents (cahier des charges, sections 5, 6, 14 et 15).

Cycle : brouillon → simulation (sans contact externe) → approbation → lancement, avec pause,
reprise et arrêt. Chaque changement d'état est daté, attribué et inscrit au journal de la mission.

Garde-fous :
* une mission doit avoir au moins un plafond (budget d'IA ou nombre total d'actions) ;
* le lancement et la reprise revérifient l'agent (non suspendu) et l'arrêt d'urgence ;
* alerte à 80 % du budget, pause automatique à 100 % ou après un nombre d'échecs de suite ;
* « 100 prospects » est un plafond, jamais une obligation : une mission peut se terminer avec
  moins de résultats, que l'agent doit alors signaler.

Les agents qui EXÉCUTENT un type de mission se branchent dans `runners` : le Prospecteur
(Lot 3) fournira celui de la prospection. Tant qu'aucun n'existe, une mission lancée reste
« en cours, en attente d'un agent » : le cycle, les limites et la supervision sont actifs, mais
aucune recherche ni aucun contact n'a lieu.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import agent_control
from .db import JournalEntry, Mission, MissionEvent, utcnow
from .governance import ALL_CHANNELS, CHANNELS, Governor

KINDS = {"prospection": "Prospection B2B",
         "autopilot": "Instruction découpée en tâches (Autopilot)"}
AUTONOMIES = {
    "validation": "Recherche autonome ; tout contact soumis à validation",
    "lecture": "Recherche seule, aucun contact",
}
STATUS_LABELS = {
    "brouillon": "Brouillon", "simule": "Simulée", "approuve": "Approuvée",
    "en_cours": "En cours", "en_pause": "En pause", "termine": "Terminée", "arrete": "Arrêtée",
}
EDITABLE = ("brouillon", "simule")
FINAL = ("termine", "arrete")
WAITING = "En attente d'un agent pour ce type de mission (aucune recherche ni contact)"
BUDGET_ALERT = 0.8
BUDGET_ALERT_TEXT = "Budget à 80 %"


class MissionError(ValueError):
    pass


@dataclass
class Check:
    label: str
    ok: bool
    detail: str
    blocking: bool = False   # une vérification bloquante en échec empêche d'avancer


@dataclass
class RunResult:
    ok: bool
    summary: str
    cost: float = 0.0
    done: bool = False       # la mission a atteint son objectif ou épuisé ses sources


Runner = Callable[[Mission], RunResult]
# Agents exécutants, par type de mission : le Prospecteur (Lot 3) y inscrira « prospection »
RUNNERS: dict[str, Runner] = {}


def _text(value: object, label: str, limit: int, required: bool = False) -> str:
    text = " ".join(str(value or "").split())[:limit]
    if required and not text:
        raise MissionError(f"{label} : champ obligatoire")
    return text


def _number(value: object, label: str, kind: type, maxi: float | None = None):
    raw = str(value if value is not None else "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        number = int(float(raw)) if kind is int else float(raw)
    except ValueError as exc:
        raise MissionError(f"{label} : nombre attendu") from exc
    if number <= 0 or (maxi is not None and number > maxi):
        raise MissionError(f"{label} : doit être positif"
                           + (f" et au plus {maxi:g}" if maxi else ""))
    return number


class MissionService:
    def __init__(self, sessions: sessionmaker[Session], governor: Governor,
                 pole_codes: set[str], runners: dict[str, Runner] | None = None) -> None:
        self._sessions = sessions
        self.governor = governor
        self.pole_codes = set(pole_codes)
        self.runners: dict[str, Runner] = RUNNERS if runners is None else dict(runners)

    # ------------------------------------------------------------------ saisie
    def _fields(self, data: dict) -> dict:
        kind = str(data.get("kind") or "prospection")
        agent = str(data.get("agent") or "prospecteur")
        autonomy = str(data.get("autonomy") or "validation")
        channel = str(data.get("channel") or "")
        pole = str(data.get("pole") or "")
        if kind not in KINDS:
            raise MissionError(f"Type de mission inconnu : {kind}")
        if agent not in agent_control.AGENTS:
            raise MissionError(f"Agent inconnu : {agent}")
        if autonomy not in AUTONOMIES:
            raise MissionError(f"Autonomie inconnue : {autonomy}")
        if channel and channel not in CHANNELS:
            raise MissionError(f"Canal inconnu : {channel}")
        if pole not in self.pole_codes:
            raise MissionError(f"Pôle inconnu : {pole or '—'}")
        threshold = _number(data.get("error_threshold") or 3, "Seuil d'échecs", int, 20)
        return {
            "name": _text(data.get("name"), "Nom de la mission", 200, required=True), "kind": kind,
            "pole": pole, "product": _text(data.get("product"), "Produit", 200),
            "zone": _text(data.get("zone"), "Zone", 200),
            "target": _text(data.get("target"), "Cible", 300),
            "objective": _text(data.get("objective"), "Objectif", 2000),
            "criteria": _text(data.get("criteria"), "Critères", 2000), "agent": agent, "channel": channel,
            "autonomy": autonomy, "budget_usd": _number(data.get("budget_usd"), "Budget (USD)", float),
            "max_per_day": _number(data.get("max_per_day"), "Volume par jour", int),
            "max_total": _number(data.get("max_total"), "Volume total", int),
            "error_threshold": int(threshold or 3)}

    def _event(self, s: Session, mission_id: int, kind: str, summary: str, by: str = "") -> None:
        s.add(MissionEvent(mission_id=mission_id, kind=kind, summary=summary[:500], by=by))

    def _mission(self, s: Session, mission_id: int) -> Mission:
        m = s.get(Mission, mission_id)
        if m is None:
            raise MissionError(f"Mission {mission_id} introuvable")
        return m

    def create(self, data: dict, by: str) -> int:
        fields = self._fields(data)
        with self._sessions() as s:
            m = Mission(created_by=by, **fields)
            s.add(m)
            s.flush()
            self._event(s, m.id, "etat", "Brouillon créé", by)
            s.commit()
            return m.id

    def update(self, mission_id: int, data: dict, by: str) -> None:
        fields = self._fields(data)
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status not in EDITABLE:
                raise MissionError("Seule une mission en brouillon ou simulée se modifie")
            for k, v in fields.items():
                setattr(m, k, v)
            m.status, m.simulated_at, m.updated_at = "brouillon", None, utcnow()
            self._event(s, m.id, "etat", "Mission modifiée : retour en brouillon (nouvelle "
                                         "simulation nécessaire)", by)
            s.commit()

    # ------------------------------------------------------------------ vérifications
    def checks(self, m: Mission) -> list[Check]:
        out = []
        suspended = agent_control.is_suspended(self._sessions, m.agent)
        out.append(Check(f"Agent « {m.agent} »", not suspended, suspended or "disponible",
                         blocking=True))
        stopped = self.governor.is_stopped(m.channel or ALL_CHANNELS)
        out.append(Check("Arrêt d'urgence", not stopped,
                         "actif : aucune action ne peut partir" if stopped else "non déclenché",
                         blocking=True))
        has_limit = m.budget_usd is not None or m.max_total is not None
        out.append(Check("Plafond de la mission", has_limit,
                         "budget ou volume total défini" if has_limit else
                         "définissez au moins un budget ou un volume total", blocking=True))
        both = m.budget_usd is not None and m.max_per_day is not None
        out.append(Check("Budget et volume par jour", both,
                         "définis" if both else "recommandés : un budget et un volume par jour"))
        if m.budget_usd is not None and m.spent_usd >= m.budget_usd:
            out.append(Check("Budget", False, "déjà épuisé", blocking=True))
        out.append(Check("Produit et zone", bool(m.product and m.zone),
                         "renseignés" if m.product and m.zone else "à préciser pour cibler"))
        ready = m.kind in self.runners
        out.append(Check("Agent exécutant", ready, "prêt" if ready else WAITING))
        out.append(Check("Contacts", True, AUTONOMIES[m.autonomy]))
        return out

    @staticmethod
    def blocking_failures(checks: list[Check]) -> list[Check]:
        return [c for c in checks if c.blocking and not c.ok]

    # ------------------------------------------------------------------ cycle de vie
    def simulate(self, mission_id: int, by: str) -> list[Check]:
        """Vérifie la mission sans aucune communication ni appel externe."""
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status not in EDITABLE:
                raise MissionError("Seule une mission en brouillon ou simulée se simule")
            checks = self.checks(m)
            failed = self.blocking_failures(checks)
            summary = "Simulation : " + ("; ".join(f"{c.label} — {c.detail}" for c in failed)
                                         if failed else "aucun blocage, mission prête à approuver")
            self._event(s, m.id, "simulation", summary, by)
            if not failed:
                m.status, m.simulated_at = "simule", utcnow()
            m.updated_at = utcnow()
            s.commit()
        return checks

    def approve(self, mission_id: int, by: str) -> None:
        if not by:
            raise MissionError("L'approbation doit être nominative")
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status != "simule" or m.simulated_at is None:
                raise MissionError("Une mission s'approuve après une simulation sans blocage")
            m.status, m.approved_by, m.approved_at = "approuve", by, utcnow()
            m.updated_at = utcnow()
            self._event(s, m.id, "etat", "Approuvée", by)
            s.commit()

    def launch(self, mission_id: int, by: str) -> None:
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status != "approuve":
                raise MissionError("Seule une mission approuvée se lance")
            failed = self.blocking_failures(self.checks(m))
            if failed:
                raise MissionError("Lancement refusé : " + "; ".join(
                    f"{c.label} — {c.detail}" for c in failed))
            m.status, m.launched_at, m.pause_reason = "en_cours", utcnow(), ""
            m.updated_at = utcnow()
            self._event(s, m.id, "etat", "Lancée", by)
            s.commit()

    def pause(self, mission_id: int, by: str, reason: str = "") -> None:
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status != "en_cours":
                raise MissionError("Seule une mission en cours se met en pause")
            self._pause(s, m, reason.strip() or "pause demandée", by)
            s.commit()

    def activate(self, mission_id: int, by: str, why: str) -> None:
        """Met en cours une mission dont le plan vient d'être validé par la direction (Autopilot :
        ses tâches sont suivies une à une, sans exécutant automatique)."""
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            now = utcnow()
            m.status, m.approved_by, m.approved_at, m.launched_at = "en_cours", by, now, now
            m.updated_at = now
            self._event(s, m.id, "etat", why, by)
            s.commit()

    def note(self, mission_id: int, summary: str, by: str) -> None:
        """Inscrit une remarque (ex. dépôt d'un fichier) à l'historique de la mission."""
        with self._sessions() as s:
            self._mission(s, mission_id)
            self._event(s, mission_id, "etat", summary, by)
            s.commit()

    def _pause(self, s: Session, m: Mission, reason: str, by: str) -> None:
        m.status, m.pause_reason, m.updated_at = "en_pause", reason[:300], utcnow()
        self._event(s, m.id, "etat", f"En pause : {reason}", by)

    def resume(self, mission_id: int, by: str) -> None:
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status != "en_pause":
                raise MissionError("Seule une mission en pause se reprend")
            failed = self.blocking_failures(self.checks(m))
            if failed:
                raise MissionError("Reprise refusée : " + "; ".join(
                    f"{c.label} — {c.detail}" for c in failed))
            m.status, m.pause_reason, m.consecutive_failures = "en_cours", "", 0
            m.updated_at = utcnow()
            self._event(s, m.id, "etat", "Reprise", by)
            s.commit()

    def stop(self, mission_id: int, by: str) -> None:
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            if m.status in FINAL:
                raise MissionError("Cette mission est déjà terminée ou arrêtée")
            m.status, m.ended_at, m.updated_at = "arrete", utcnow(), utcnow()
            self._event(s, m.id, "etat", "Arrêtée", by)
            s.commit()

    # ------------------------------------------------------------------ exécution
    def _alert(self, s: Session, m: Mission, summary: str) -> None:
        self._event(s, m.id, "alerte", summary)
        s.add(JournalEntry(agent="missions", action_type="mission.alerte", level=1,
                           channel="interne", pole=m.pole, status="flagged",
                           summary=f"Mission « {m.name} » : {summary}",
                           details={"mission_id": m.id}))

    def record_run(self, mission_id: int, result: RunResult) -> None:
        """Compte une exécution, ses coûts et ses échecs ; alerte, met en pause ou termine."""
        with self._sessions() as s:
            m = self._mission(s, mission_id)
            m.runs += 1
            m.spent_usd += max(result.cost, 0.0)
            m.last_result, m.updated_at = result.summary[:500], utcnow()
            self._event(s, m.id, "resultat" if result.ok else "alerte", result.summary)
            if result.ok:
                m.consecutive_failures = 0
            else:
                m.failures += 1
                m.consecutive_failures += 1
            if m.status == "en_cours":
                self._enforce(s, m, result)
            s.commit()

    def _enforce(self, s: Session, m: Mission, result: RunResult) -> None:
        if m.budget_usd is not None:
            if m.spent_usd >= m.budget_usd:
                self._alert(s, m, f"budget épuisé ({m.spent_usd:.2f}/{m.budget_usd:.2f} USD)")
                self._pause(s, m, "budget atteint", "système")
                return
            if m.spent_usd >= BUDGET_ALERT * m.budget_usd:
                already = s.scalar(select(func.count()).select_from(MissionEvent).where(
                    MissionEvent.mission_id == m.id, MissionEvent.kind == "alerte",
                    MissionEvent.summary.startswith(BUDGET_ALERT_TEXT))) or 0
                if not already:
                    self._alert(s, m, f"{BUDGET_ALERT_TEXT} ({m.spent_usd:.2f}/"
                                      f"{m.budget_usd:.2f} USD)")
        if m.consecutive_failures >= m.error_threshold:
            self._alert(s, m, f"{m.consecutive_failures} échecs de suite")
            self._pause(s, m, f"{m.consecutive_failures} échecs de suite", "système")
            return
        if m.max_total is not None and m.runs >= m.max_total:
            m.status, m.ended_at = "termine", utcnow()
            self._event(s, m.id, "etat", f"Terminée : volume total atteint ({m.max_total})",
                        "système")
        elif result.done:
            m.status, m.ended_at = "termine", utcnow()
            self._event(s, m.id, "etat", "Terminée : objectif atteint ou sources épuisées",
                        "système")

    def tick(self, now: datetime | None = None) -> dict[str, int]:
        """Surveille les missions en cours (tâche planifiée) : arrêts, suspension de l'agent,
        volume du jour, puis exécution par l'agent branché sur ce type de mission."""
        now = now or utcnow()
        stats = {"surveillees": 0, "mises_en_pause": 0, "executees": 0, "en_attente": 0}
        with self._sessions() as s:
            ids = list(s.scalars(select(Mission.id).where(Mission.status == "en_cours")).all())
        for mid in ids:
            stats["surveillees"] += 1
            with self._sessions() as s:
                m = self._mission(s, mid)
                reason = ""
                if self.governor.is_stopped(m.channel or ALL_CHANNELS):
                    reason = "arrêt d'urgence"
                elif agent_control.is_suspended(self._sessions, m.agent):
                    reason = "agent suspendu"
                if reason:
                    self._pause(s, m, reason, "système")
                    s.commit()
                    stats["mises_en_pause"] += 1
                    continue
                if m.kind == "autopilot":   # ses tâches sont suivies à la main, pas par un passage
                    continue
                runner = self.runners.get(m.kind)
                if runner is None:
                    last = s.scalar(select(MissionEvent.summary).where(
                        MissionEvent.mission_id == m.id).order_by(
                        MissionEvent.id.desc()).limit(1))
                    if last != WAITING:   # une seule fois : pas de bruit à chaque passage
                        self._event(s, m.id, "etat", WAITING, "système")
                        s.commit()
                    stats["en_attente"] += 1
                    continue
                done_today = s.scalar(select(func.count()).select_from(MissionEvent).where(
                    MissionEvent.mission_id == m.id,
                    MissionEvent.kind.in_(("resultat", "alerte")),
                    MissionEvent.at >= agent_control.day_start(now))) or 0
                if m.max_per_day is not None and done_today >= m.max_per_day:
                    continue
                s.expunge(m)
            try:
                result = runner(m)
            except Exception as exc:  # noqa: BLE001 — l'échec est compté, la mission continue
                result = RunResult(False, f"Échec de l'agent : {exc}"[:500])
            self.record_run(mid, result)
            stats["executees"] += 1
        return stats

    # ------------------------------------------------------------------ lecture
    def all_missions(self, status: str = "", limit: int = 200) -> list[Mission]:
        with self._sessions() as s:
            q = select(Mission)
            if status:
                q = q.where(Mission.status == status)
            return list(s.scalars(q.order_by(Mission.id.desc()).limit(limit)).all())

    def get(self, mission_id: int) -> Mission | None:
        with self._sessions() as s:
            return s.get(Mission, mission_id)

    def events(self, mission_id: int, limit: int = 100) -> list[MissionEvent]:
        with self._sessions() as s:
            return list(s.scalars(select(MissionEvent).where(
                MissionEvent.mission_id == mission_id).order_by(
                MissionEvent.id.desc()).limit(limit)).all())
