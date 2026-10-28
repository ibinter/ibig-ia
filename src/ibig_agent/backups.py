"""État des sauvegardes, écrit par deploy/sauvegarde.sh dans state/sauvegarde.json.

Le script tourne sur le serveur (cron), hors du conteneur : le tableau de bord, le
diagnostic et la tâche de contrôle lisent ce fichier pour savoir si la dernière
sauvegarde a réussi, si elle est chiffrée et si elle a été copiée hors du serveur.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MAX_AGE_HOURS = 48     # au-delà, la direction est alertée (sauvegarde quotidienne à 2 h 15)


@dataclass
class BackupStatus:
    found: bool
    date: datetime | None = None
    ok: bool = False
    file: str = ""
    size: int = 0
    encrypted: bool = False
    offsite: str = ""
    offsite_ok: bool = False
    message: str = ""
    problems: list[str] = field(default_factory=list)   # bloquants (alerte)
    advice: list[str] = field(default_factory=list)     # à améliorer

    @property
    def age_hours(self) -> float | None:
        if self.date is None:
            return None
        return (datetime.now(UTC) - self.date).total_seconds() / 3600

    @property
    def level(self) -> str:
        """ok | warn | ko : la couleur affichée au tableau de bord."""
        if not self.found or self.problems:
            return "ko"
        return "warn" if self.advice else "ok"


def read_status(state_dir: Path) -> BackupStatus:
    path = Path(state_dir) / "sauvegarde.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BackupStatus(found=False, problems=[
            "Aucune sauvegarde enregistrée : programmer deploy/sauvegarde.sh (cron de 2 h 15)"])
    try:
        when = datetime.fromisoformat(str(raw.get("date", "")))
        when = when if when.tzinfo else when.replace(tzinfo=UTC)
    except ValueError:
        when = None
    st = BackupStatus(
        found=True, date=when, ok=raw.get("etat") == "ok", file=str(raw.get("fichier", "")),
        size=int(raw.get("taille") or 0), encrypted=bool(raw.get("chiffree")),
        offsite=str(raw.get("hors_serveur", "")), offsite_ok=bool(raw.get("copie_ok")),
        message=str(raw.get("message", ""))[:300])
    if not st.ok:
        st.problems.append(st.message or "La dernière sauvegarde a échoué")
    age = st.age_hours
    if age is None or age > MAX_AGE_HOURS:
        st.problems.append("Aucune sauvegarde depuis plus de 48 h : vérifier la tâche cron "
                           "et /var/log/ibig-sauvegarde.log")
    if not st.encrypted:
        st.advice.append("Archive non chiffrée : lancer ./deploy/sauvegarde.sh --init")
    if not st.offsite:
        st.advice.append("Pas de copie hors du serveur : renseigner IBIG_BACKUP_RCLONE ou "
                         "IBIG_BACKUP_SSH dans .env")
    return st


def check(state_dir: Path, production: bool = True) -> str:
    """Tâche planifiée : lève une erreur si la sauvegarde n'est pas à jour (alerte après
    plusieurs contrôles en échec, comme toute tâche suivie). Hors production (poste de
    développement, démonstration), l'absence de sauvegarde n'est pas une erreur."""
    st = read_status(state_dir)
    if not st.found and not production:
        return "Pas de sauvegarde suivie hors production"
    if st.problems:
        raise RuntimeError(st.problems[0])
    return "Dernière sauvegarde réussie" + (" · " + " · ".join(st.advice) if st.advice else "")
