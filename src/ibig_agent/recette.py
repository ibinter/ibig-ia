"""Mesure du critère R-02 (section 17) : « 95 % de bons classements sur un échantillon de
200 mails ».

1. `ibig-agent recette echantillon` tire au hasard des mails traités et écrit un fichier
   CSV (sans adresse d'expéditeur : minimisation des données) ;
2. une personne remplit `pole_attendu` et `categorie_attendue` en consultant les mails ;
3. `ibig-agent recette score fichier.csv` calcule la justesse, liste les erreurs et dit
   si le critère est atteint.
"""

from __future__ import annotations

import csv
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .agents.messagerie import CATEGORIES
from .db import ProcessedMessage, utcnow

COLUMNS = ["id", "date", "boite", "objet", "resume", "pole_agent", "categorie_agent",
           "pole_attendu", "categorie_attendue", "commentaire"]
TARGET = 0.95


def write_sample(sessions: sessionmaker[Session], path: Path, size: int = 200,
                 days: int = 30, seed: int | None = None, now: datetime | None = None) -> int:
    since = (now or utcnow()) - timedelta(days=days)
    with sessions() as s:
        rows = s.scalars(select(ProcessedMessage).where(
            ProcessedMessage.processed_at >= since,
            ProcessedMessage.category != "",  # mails réellement classés par l'agent
        )).all()
    sample = random.Random(seed).sample(rows, min(size, len(rows)))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for m in sorted(sample, key=lambda m: m.id):
            w.writerow([m.id, f"{m.received_at or m.processed_at:%Y-%m-%d %H:%M}", m.mailbox,
                        m.subject, m.summary, m.pole, m.category, "", "", ""])
    return len(sample)


@dataclass
class Score:
    total: int = 0
    pole_ok: int = 0
    categorie_ok: int = 0
    both_ok: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)
    confusions: Counter = field(default_factory=Counter)
    invalid: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.both_ok / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        return self.total >= 200 and self.accuracy >= TARGET

    def as_text(self) -> str:
        if not self.total:
            return "Aucune ligne remplie : compléter pole_attendu et categorie_attendue."
        def pct(n: int) -> str:
            return f"{100 * n / self.total:.1f} %"

        ignored = f", {self.ignored} ligne(s) non remplie(s) ignorée(s)" if self.ignored else ""
        lines = [
            f"Critère R-02 — {self.total} mail(s) vérifié(s){ignored}",
            (f"Pôle juste : {pct(self.pole_ok)} · Type juste : {pct(self.categorie_ok)} · "
             f"Les deux : {pct(self.both_ok)} (cible {TARGET:.0%})"),
        ]
        if self.total < 200:
            lines.append(f"Échantillon insuffisant : 200 mails demandés, {self.total} vérifiés.")
        lines.append("RÉSULTAT : " + ("ATTEINT" if self.passed else "NON ATTEINT"))
        if self.confusions:
            lines += ["", "Confusions les plus fréquentes (agent → attendu) :"]
            lines += [f"- {a} → {b} : {n}" for (a, b), n in self.confusions.most_common(8)]
        if self.errors:
            lines += ["", "Erreurs :"] + [f"- {e}" for e in self.errors[:30]]
        if self.invalid:
            lines += ["", "Valeurs non reconnues (lignes ignorées) :"]
            lines += [f"- {e}" for e in self.invalid]
        return "\n".join(lines)


def score_file(path: Path, pole_codes: list[str]) -> Score:
    score = Score()
    text = Path(path).read_text(encoding="utf-8-sig")
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") \
        else ","
    for row in csv.DictReader(text.splitlines(), delimiter=delimiter):
        expected_pole = (row.get("pole_attendu") or "").strip().upper()
        expected_cat = (row.get("categorie_attendue") or "").strip().lower()
        if not expected_pole and not expected_cat:
            score.ignored += 1
            continue
        # Case laissée vide = la personne valide la valeur de l'agent.
        expected_pole = expected_pole or row["pole_agent"]
        expected_cat = expected_cat or row["categorie_agent"]
        if expected_pole not in pole_codes or expected_cat not in CATEGORIES:
            score.invalid.append(f"ligne {row.get('id')} : {expected_pole} / {expected_cat}")
            continue
        score.total += 1
        pole_ok = expected_pole == row["pole_agent"]
        cat_ok = expected_cat == row["categorie_agent"]
        score.pole_ok += pole_ok
        score.categorie_ok += cat_ok
        score.both_ok += pole_ok and cat_ok
        if not cat_ok:
            score.confusions[(row["categorie_agent"], expected_cat)] += 1
        if not (pole_ok and cat_ok):
            score.errors.append(
                f"n° {row.get('id')} « {row.get('objet', '')[:60]} » : agent "
                f"{row['pole_agent']}/{row['categorie_agent']}, attendu "
                f"{expected_pole}/{expected_cat}")
    return score
