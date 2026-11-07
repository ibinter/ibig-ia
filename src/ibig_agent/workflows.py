"""Workflows approuvables (cahier des charges v2, 7.2).

Une définition de workflow est un plan de tâches (agent, dépendances, preuve attendue) que la
direction peut créer, faire approuver, puis lancer autant de fois que nécessaire. Règles :
  - une version approuvée n'est plus modifiable ; on crée une nouvelle version ;
  - la personne qui a écrit la version ne l'approuve pas, sauf s'il n'y a qu'un seul responsable ;
  - seule une version approuvée se lance ; l'approbation d'une nouvelle version archive la précédente ;
  - le lancement crée une mission Autopilot (aucun appel d'IA) ; rien n'est envoyé ni publié.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import agent_control, procedures
from .autopilot import MAX_TASKS, Autopilot, PlannedTask
from .db import User, WorkflowDef, utcnow


class WorkflowError(ValueError):
    pass


def clean_steps(raw: list) -> list[dict]:
    """Contrôle et normalise les étapes ; lève WorkflowError si elles ne sont pas exploitables."""
    if not isinstance(raw, list) or not raw:
        raise WorkflowError("Un workflow contient au moins une étape")
    if len(raw) > MAX_TASKS:
        raise WorkflowError(f"Trop d'étapes ({len(raw)}, maximum {MAX_TASKS})")
    out: list[dict] = []
    for n, st in enumerate(raw, 1):
        title = " ".join(str(st.get("title", "")).split())[:300]
        if not title:
            raise WorkflowError(f"L'étape {n} n'a pas de titre")
        agent = str(st.get("agent", "")).strip().lower()
        if agent and agent not in agent_control.AGENTS:
            raise WorkflowError(f"Étape {n} : agent inconnu « {agent} »")
        after = sorted({int(a) for a in st.get("after", [])})
        if any(a < 1 or a >= n for a in after):
            raise WorkflowError(f"Étape {n} : dépend d'une étape inexistante ou postérieure")
        out.append({"title": title, "agent": agent, "after": after,
                    "proof": " ".join(str(st.get("proof", "")).split())[:300]})
    return out


def parse_text(text: str) -> list[dict]:
    """Une étape par ligne : « Titre | agent | après (ex. 1,2) | preuve attendue »."""
    steps = []
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")] + ["", "", ""]
        after = [int(x) for x in parts[2].replace(" ", "").split(",") if x.isdigit()]
        steps.append({"title": parts[0], "agent": parts[1], "after": after, "proof": parts[3]})
    return steps


class WorkflowService:
    def __init__(self, sessions: sessionmaker[Session], autopilot: Autopilot) -> None:
        self._sessions = sessions
        self.autopilot = autopilot

    # ------------------------------------------------------------------ lecture
    def all(self) -> list[WorkflowDef]:
        with self._sessions() as s:
            return list(s.scalars(select(WorkflowDef).order_by(
                WorkflowDef.code, WorkflowDef.version.desc())).all())

    def get(self, workflow_id: int) -> WorkflowDef | None:
        with self._sessions() as s:
            return s.get(WorkflowDef, workflow_id)

    def approved(self) -> dict[str, WorkflowDef]:
        """Dernière version approuvée de chaque workflow, par code."""
        with self._sessions() as s:
            rows = s.scalars(select(WorkflowDef).where(WorkflowDef.status == "approuve")).all()
        return {w.code: w for w in rows}

    # ------------------------------------------------------------------ création
    def create(self, code: str, title: str, trigger: str, pole: str, steps: list, by: str) -> int:
        """Crée un brouillon ; si le code existe déjà, c'est la version suivante."""
        code = "-".join(code.lower().split())[:60]
        title = " ".join(title.split())[:200]
        if not code or not title:
            raise WorkflowError("Donnez un code et un titre")
        cleaned = clean_steps(steps)
        with self._sessions() as s:
            version = (s.scalar(select(func.max(WorkflowDef.version)).where(
                WorkflowDef.code == code)) or 0) + 1
            row = WorkflowDef(code=code, version=version, title=title, trigger=trigger[:300],
                              pole=pole, steps=cleaned, created_by=by)
            s.add(row)
            s.commit()
            return row.id

    def adopt(self, procedure_code: str, by: str) -> int:
        """Copie une procédure de la bibliothèque en brouillon de workflow modifiable."""
        proc = procedures.get(procedure_code)
        if proc is None:
            raise WorkflowError("Procédure inconnue")
        steps = [{"title": st.title, "agent": st.agent, "after": list(st.after), "proof": st.proof}
                 for st in proc.steps]
        return self.create(proc.code, proc.title, proc.trigger, "", steps, by)

    # ------------------------------------------------------------------ cycle de vie
    def approve(self, workflow_id: int, by: str) -> None:
        with self._sessions() as s:
            w = s.get(WorkflowDef, workflow_id)
            if w is None:
                raise WorkflowError("Workflow introuvable")
            if w.status != "brouillon":
                raise WorkflowError("Seul un brouillon peut être approuvé")
            managers = s.scalar(select(func.count()).select_from(User).where(
                User.active.is_(True), User.role.in_(("admin", "direction")))) or 0
            if w.created_by == by and managers > 1:
                raise WorkflowError("Une autre personne doit approuver ce workflow")
            for old in s.scalars(select(WorkflowDef).where(
                    WorkflowDef.code == w.code, WorkflowDef.status == "approuve")):
                old.status = "archive"
            w.status, w.approved_by, w.approved_at = "approuve", by, utcnow()
            s.commit()

    def archive(self, workflow_id: int) -> None:
        with self._sessions() as s:
            w = s.get(WorkflowDef, workflow_id)
            if w is None:
                raise WorkflowError("Workflow introuvable")
            w.status = "archive"
            s.commit()

    # ------------------------------------------------------------------ lancement
    def launch(self, workflow_id: int, subject: str, by: str, pole: str = "") -> int:
        with self._sessions() as s:
            w = s.get(WorkflowDef, workflow_id)
            if w is None:
                raise WorkflowError("Workflow introuvable")
            if w.status != "approuve":
                raise WorkflowError("Seul un workflow approuvé peut être lancé")
            title, steps, version, wpole = w.title, list(w.steps), w.version, w.pole
        subject = " ".join(subject.split())[:200]
        if not subject:
            raise WorkflowError("Précisez le sujet (nom du client, de la session…)")
        tasks = [PlannedTask(st["title"], f"Preuve attendue : {st['proof']}" if st["proof"] else "",
                             st["agent"], list(st["after"])) for st in steps]
        return self.autopilot.create_from(f"{title} — {subject}", f"{title} ({subject})", tasks,
                                          pole or wpole, by,
                                          f"Workflow « {title} » v{version} : {subject}")
