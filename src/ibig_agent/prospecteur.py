"""Agent Prospecteur (cahier des charges, Lot 3) : collecte, déduplication et score explicable.

Sources autorisées (décision de la direction) :
  - fichiers CSV déposés pour une mission (`<dossier>/mission_<id>.csv`) : exports de réseaux
    sociaux, de Google, d'annuaires, listes achetées légalement ;
  - pages publiques d'annuaires ou de sites d'entreprise (adresse dans « Cible » de la mission),
    lues seulement si le fichier robots.txt du site l'autorise.
Aucune collecte automatique de Google, Facebook, LinkedIn ou autres réseaux : leurs conditions
l'interdisent. Leurs données entrent par fichier ou par API officielle (non raccordée).

Le Prospecteur ne contacte personne : il crée des fiches, avec leur origine et leur score, que
l'agent Commercial et les humains traitent ensuite.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .db import Mission, Prospect, ProspectActivity, utcnow
from .missions import RunResult

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
FREE_MAIL = {"gmail.com", "yahoo.fr", "yahoo.com", "hotmail.com", "hotmail.fr", "outlook.com",
             "outlook.fr", "live.fr", "icloud.com"}
NOISE_LOCALS = {"noreply", "no-reply", "donotreply", "postmaster", "webmaster", "abuse"}
IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
COLUMNS = {"email": ("email", "e-mail", "mail", "courriel"),
           "name": ("nom", "name", "contact", "prenom nom"),
           "company": ("entreprise", "societe", "société", "company", "organisation"),
           "phone": ("telephone", "téléphone", "tel", "phone", "mobile"),
           "website": ("site", "site web", "website", "url"),
           "city": ("ville", "city", "zone", "localisation", "adresse"),
           "origin": ("source", "origine", "reseau", "réseau"),
           "origin_url": ("lien", "profil", "url source", "source_url")}


@dataclass
class Candidate:
    email: str
    name: str = ""
    company: str = ""
    phone: str = ""
    website: str = ""
    city: str = ""
    origin: str = ""
    origin_url: str = ""
    reasons: list[str] = field(default_factory=list)
    score: int = 0


def clean_email(raw: str) -> str:
    mail = (raw or "").strip().strip("<>;,.").lower()
    if not EMAIL_RE.fullmatch(mail) or mail.endswith(IMAGE_SUFFIX):
        return ""
    if mail.split("@")[0] in NOISE_LOCALS:
        return ""
    return mail


def parse_csv(text: str) -> list[Candidate]:
    """Lit un CSV (séparateur , ou ;) en reconnaissant les colonnes par leur titre."""
    text = text.lstrip("﻿")
    delim = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    index: dict[str, str] = {}
    for col in reader.fieldnames or []:
        key = col.strip().lower()
        for target, names in COLUMNS.items():
            if key in names and target not in index:
                index[target] = col
    out = []
    for row in reader:
        values = {t: (row.get(c) or "").strip() for t, c in index.items()}
        mail = clean_email(values.pop("email", ""))
        if mail:
            out.append(Candidate(email=mail, **values))
    return out


def fetch_public_page(url: str, client: httpx.Client | None = None) -> tuple[list[Candidate], str]:
    """Lit une page publique si robots.txt l'autorise ; renvoie (candidats, remarque)."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return [], "adresse de source invalide"
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=True,
                                    headers={"User-Agent": "IBIG-Prospecteur/1.0"})
    try:
        robots = RobotFileParser()
        try:
            resp = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
            robots.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError:
            robots.parse([])
        if not robots.can_fetch("IBIG-Prospecteur", url):
            return [], "lecture interdite par le robots.txt du site"
        page = client.get(url)
        page.raise_for_status()
        html = page.text
    finally:
        if own:
            client.close()
    seen, out = set(), []
    for found in EMAIL_RE.findall(html):
        mail = clean_email(found)
        if mail and mail not in seen:
            seen.add(mail)
            out.append(Candidate(email=mail, website=f"{parts.scheme}://{parts.netloc}",
                                 origin="page publique", origin_url=url))
    return out, ""


def score(c: Candidate, m: Mission) -> Candidate:
    """Score sur 100, chaque point est expliqué (liste de raisons conservée sur la fiche)."""
    points, why = 0, []
    if c.email.split("@")[1] not in FREE_MAIL:
        points += 25
        why.append("adresse professionnelle (+25)")
    else:
        why.append("adresse personnelle (+0)")
    for have, label, pts in ((c.name, "nom connu", 10), (c.company, "entreprise connue", 10),
                             (c.phone, "téléphone connu", 10), (c.website, "site connu", 5),
                             (c.origin_url, "source vérifiable", 10)):
        if have:
            points += pts
            why.append(f"{label} (+{pts})")
    zone = (m.zone or "").lower()
    if zone and zone in f"{c.city} {c.company} {c.website}".lower():
        points += 15
        why.append(f"zone « {m.zone} » (+15)")
    words = [w for w in re.split(r"\W+", (m.target or "").lower()) if len(w) > 3]
    blob = f"{c.name} {c.company} {c.website}".lower()
    if words and any(w in blob for w in words):
        points += 15
        why.append("correspond à la cible (+15)")
    c.score, c.reasons = min(points, 100), why
    return c


def ingest(sessions: sessionmaker, m: Mission, candidates: list[Candidate]) -> dict[str, int]:
    """Déduplique (dans le lot et face au CRM), note et enregistre les nouvelles fiches."""
    stats = {"recus": len(candidates), "crees": 0, "doublons": 0}
    batch: dict[str, Candidate] = {}
    for c in candidates:
        old = batch.get(c.email)
        if old is None:
            batch[c.email] = c
            continue
        stats["doublons"] += 1
        for attr in ("name", "company", "phone", "website", "city", "origin_url"):
            if not getattr(old, attr) and getattr(c, attr):
                setattr(old, attr, getattr(c, attr))
    with sessions() as s:
        existing = set(s.scalars(select(Prospect.email).where(
            Prospect.email.in_(list(batch)))).all()) if batch else set()
        now = utcnow()
        for mail, c in batch.items():
            if mail in existing:
                stats["doublons"] += 1
                continue
            score(c, m)
            p = Prospect(
                email=mail, name=c.name or c.company, pole=m.pole,
                source=f"Mission {m.id} · {c.origin or 'fichier'}"[:200],
                need=m.product or m.objective[:200], score=c.score,
                source_url=c.origin_url or c.website, collected_at=now,
                verification="non_verifie", assigned_agent="prospecteur")
            s.add(p)
            s.flush()
            s.add(ProspectActivity(
                prospect_id=p.id, kind="systeme", by="prospecteur",
                summary=f"Collecté (mission {m.id}) · score {c.score}/100 : "
                        f"{', '.join(c.reasons)}"[:500]))
            stats["crees"] += 1
        s.commit()
    return stats


def make_runner(sessions: sessionmaker, directory: Path, client: httpx.Client | None = None):
    """Exécution d'une mission de prospection : un passage lit les sources de la mission."""
    def run(m: Mission) -> RunResult:
        found: list[Candidate] = []
        notes: list[str] = []
        fetched = False   # une page publique n'est lue qu'une fois : pas de relecture toutes les 5 min
        path = directory / f"mission_{m.id}.csv"
        if path.exists():
            found += parse_csv(path.read_text(encoding="utf-8", errors="replace"))
            path.rename(path.with_suffix(".csv.traite"))
        target = (m.target or "").strip()
        if target.startswith(("http://", "https://")):
            try:
                page, note = fetch_public_page(target, client)
            except httpx.HTTPError as exc:
                return RunResult(False, f"Source illisible ({target}) : {exc}"[:300])
            found += page
            fetched = True
            if note:
                notes.append(note)
        if not found:
            hint = f" ({'; '.join(notes)})" if notes else ""
            return RunResult(True, f"Aucune nouvelle donnée à traiter{hint}", done=fetched)
        if m.max_per_day:
            found = found[:m.max_per_day]
        st = ingest(sessions, m, found)
        return RunResult(True, f"{st['crees']} fiche(s) créée(s), {st['doublons']} doublon(s) "
                               f"écarté(s) sur {st['recus']} reçue(s)", done=fetched)

    return run
