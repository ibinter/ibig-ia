"""Import du catalogue complet des produits IBIG (section 7), depuis l'API publique d'IBIG PARTNERS.

IBIG PARTNERS rassemble les produits de toutes les marques (formations, solutions, services…).
Son API publique `GET /api/catalogue/<marque>` donne, pour chaque produit : nom, type de tarif,
prix, adresse et description. Ce module en tire des fichiers Markdown de type « produits » dans
`knowledge/produits/`, que l'agent consulte PAR RECHERCHE (jamais en bloc : il y a des milliers
d'entrées).

Règles suivies, pour ne rien faire dire de faux à l'agent :
* le taux de commission des partenaires (`rate`) n'est JAMAIS recopié : information interne ;
* le tarif « présentiel » et la grille de groupe du catalogue EDUFORM sont calculés par une
  formule de PARTNERS, pas publiés par le site de la marque : ils ne sont pas repris ;
* un même produit présent plusieurs fois est fusionné ; si ses tarifs diffèrent, l'entrée le dit
  (« tarif à confirmer ») au lieu de choisir un prix ;
* un prix absent ou nul n'est jamais présenté comme « 0 FCFA ».
Les fichiers sont régénérés à chaque import (ils ne se modifient pas à la main).
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE_URL = "https://ibigpartners.com"
SITE_EDUFORM = "https://ibig-eduform.com"
SUBDIR = "produits"
JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)
# (identifiant de la marque chez PARTNERS, pôle de l'agent, nom affiché)
BRANCHES = [
    ("ibig-soft", "SOFT", "IBIG SOFT"),
    ("ibig-eduform", "EDUFORM", "IBIG EDUFORM"),
    ("ibig-elearning", "EDUFORM", "IBIG eLEARNING"),
    ("ibig-immo-trust", "IMMOTRUST", "IBIG IMMO TRUST"),
    ("ibig-market", "MARKET", "IBIG MARKET"),
    ("ibig-digital", "DIGITAL", "IBIG DIGITAL"),
    ("ibig-digital-kits", "DIGITAL", "IBIG DIGITAL KITS"),
    ("ibig-conseil-plus", "CONSEIL", "IBIG CONSEIL+"),
    ("ibig-multiservices", "MULTISERVICES", "IBIG MULTISERVICES"),
    ("ibig-emploi-talents", "MULTISERVICES", "IBIG EMPLOI & TALENTS"),
    ("ibig-partners-branch", "PARTNERS", "IBIG PARTNERS"),
]
KIND = {"COURSE": "formation", "SERVICE": "service", "PRODUCT": "produit",
        "MONTHLY_SUB": "abonnement mensuel", "ANNUAL_SUB": "abonnement annuel"}
UNIT = {"MONTHLY_SUB": "par mois", "ANNUAL_SUB": "par an"}


class CatalogError(RuntimeError):
    pass


@dataclass
class Item:
    name: str
    kind: str
    prices: list[int]
    urls: list[str]
    description: str
    category: str = ""
    origin: str = "partners"   # « site » : page de formation absente du catalogue PARTNERS


@dataclass
class Result:
    files: dict[str, str] = field(default_factory=dict)       # chemin relatif -> texte
    counts: dict[str, int] = field(default_factory=dict)      # marque -> produits importés
    raw_counts: dict[str, int] = field(default_factory=dict)  # marque -> produits reçus
    ambiguous: dict[str, int] = field(default_factory=dict)   # marque -> entrées « à confirmer »
    skipped: list[str] = field(default_factory=list)          # marques sans produit
    site_added: int = 0    # formations lues sur le site EDUFORM, absentes de PARTNERS
    site_failed: int = 0   # pages du site illisibles (ignorées)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


# ------------------------------------------------------------------ lecture de l'API
def _get(client: httpx.Client, path: str) -> dict:
    last: Exception | None = None
    for _ in range(2):
        try:
            resp = client.get(BASE_URL + path)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
    raise CatalogError(f"{path} : {type(last).__name__} {last}")


def fetch_branch(client: httpx.Client, slug: str) -> list[dict]:
    """Produits actifs d'une marque ; EDUFORM vient de /api/catalogue, qui donne aussi la
    catégorie de chaque formation."""
    if slug == "ibig-eduform":
        rows = _get(client, "/api/catalogue").get("formations") or []
        return [{**r, "pricingType": r.get("pricingType") or "COURSE"} for r in rows]
    return list(_get(client, f"/api/catalogue/{slug}").get("products") or [])


def parse_course(page: str, url: str) -> dict | None:
    """Données structurées (schema.org « Course ») d'une page de formation : nom, description,
    prix, domaine du site. None si la page n'en contient pas."""
    for block in JSON_LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Course" and data.get("name"):
            offer = data.get("offers") if isinstance(data.get("offers"), dict) else {}
            try:
                price = int(float(offer.get("price") or 0))
            except (TypeError, ValueError):
                price = 0
            return {"name": data["name"], "description": data.get("description") or "",
                    "price": price, "siteUrl": url, "category": data.get("teaches") or "",
                    "pricingType": "COURSE", "_origin": "site"}
    return None


def fetch_site_extras(client: httpx.Client, known_rows: list[dict],
                      delay: float = 0.1) -> tuple[list[dict], int]:
    """Formations du site EDUFORM (plan du site) que le catalogue PARTNERS ne contient pas.
    Une demande à la fois, avec un court délai ; une page illisible est ignorée."""
    try:
        resp = client.get(f"{SITE_EDUFORM}/sitemap.xml")
        resp.raise_for_status()
    except httpx.HTTPError:
        return [], 0
    urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", resp.text) if "/formation/" in u]
    have = {str(r.get("siteUrl", "")).rstrip("/") for r in known_rows}
    rows, failed = [], 0
    for url in dict.fromkeys(u.rstrip("/") for u in urls if u.rstrip("/") not in have):
        try:
            page = client.get(url)
            page.raise_for_status()
            row = parse_course(page.text, url)
        except httpx.HTTPError:
            row = None
        if row is None:
            failed += 1
        else:
            rows.append(row)
        if delay:
            time.sleep(delay)
    return rows, failed


# ------------------------------------------------------------------ mise en forme
def _clean(text: object) -> str:
    return " ".join(str(text or "").replace("#", "").split())


def _key(name: str) -> str:
    folded = unicodedata.normalize("NFD", name.casefold())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in folded if unicodedata.category(c) != "Mn")).strip()


def _slug(text: str) -> str:
    return _key(text).replace(" ", "-")[:50] or "autres"


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " FCFA"


def merge(raw: list[dict]) -> list[Item]:
    """Fusionne les produits de même nom ; garde tous les tarifs et toutes les adresses."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in raw:
        name = _clean(p.get("name"))
        if name:
            groups[_key(name)].append(p)
    items = []
    for rows in groups.values():
        prices = sorted({int(p["price"]) for p in rows if p.get("price")})
        urls = list(dict.fromkeys(str(p["siteUrl"]) for p in rows if p.get("siteUrl")))[:3]
        desc = max((_clean(p.get("description")) for p in rows), key=len, default="")
        origin = "site" if all(p.get("_origin") == "site" for p in rows) else "partners"
        items.append(Item(_clean(rows[0]["name"]), str(rows[0].get("pricingType") or ""),
                          prices, urls, desc, _clean(rows[0].get("category")), origin))
    return sorted(items, key=lambda i: _key(i.name))


def price_line(item: Item) -> tuple[str, bool]:
    """Texte du prix, et vrai si le tarif est ambigu ou absent (à confirmer)."""
    unit = UNIT.get(item.kind, "")
    if not item.prices:
        return "non renseigné au catalogue : à confirmer avec l'équipe", True
    if len(item.prices) == 1:
        return f"{money(item.prices[0])} {unit}".strip(), False
    tarifs = " ; ".join(f"{money(v)} {unit}".strip() for v in item.prices)
    return f"plusieurs tarifs au catalogue ({tarifs}) : tarif à confirmer avec l'équipe", True


def render_item(item: Item, brand: str) -> tuple[str, bool]:
    price, doubtful = price_line(item)
    lines = [f"### {item.name}", f"- Marque : {brand}"]
    if item.category:
        label = "Domaine (indiqué par le site)" if item.origin == "site" \
            else "Domaine (classement automatique)"
        lines.append(f"- {label} : {item.category}")
    lines.append(f"- Type : {KIND.get(item.kind, 'offre')}")
    lines.append(f"- Prix : {price}")
    lines += [f"- Lien : {u}" for u in item.urls]
    if item.description:
        lines.append(f"- Description : {item.description}")
    if item.origin == "site":
        lines.append("- Source : page de la formation sur ibig-eduform.com "
                     "(absente du catalogue IBIG PARTNERS)")
    return "\n".join(lines), doubtful


def render_file(title: str, pole: str, brand: str, source: str, items: list[Item],
                today: str) -> tuple[str, int]:
    body, doubtful = [], 0
    for item in items:
        text, flag = render_item(item, brand)
        body.append(text)
        doubtful += flag
    head = (
        f"---\ntitre: {title}\npole: {pole}\ntype: produits\nsource: {source}\n"
        f"importe_le: {today}\n---\n"
        f"Catalogue {brand} : {len(items)} entrée(s), importées le {today} depuis le catalogue "
        "IBIG PARTNERS (tel qu'il est publié). Le prix d'une entrée marquée « à confirmer » ne "
        "doit pas être annoncé à un client sans vérification par l'équipe.\n\n"
    )
    return head + "\n\n".join(body) + "\n", doubtful


def build(client: httpx.Client, today: str | None = None, delay: float = 0.1,
          with_site: bool = True) -> Result:
    today = today or datetime.now(UTC).date().isoformat()
    out = Result()
    for slug, pole, brand in BRANCHES:
        raw = fetch_branch(client, slug)
        out.raw_counts[brand] = len(raw)
        if slug == "ibig-eduform" and with_site and raw:
            # Le site présente des formations que le catalogue PARTNERS n'a pas (encore)
            extra, out.site_failed = fetch_site_extras(client, raw, delay)
            out.site_added = len(extra)
            raw = raw + extra
        items = merge(raw)
        if not items:
            out.skipped.append(brand)
            continue
        out.counts[brand] = len(items)
        out.ambiguous[brand] = 0
        source = f"{BASE_URL}/api/catalogue" + ("" if slug == "ibig-eduform" else f"/{slug}")
        if slug == "ibig-eduform":  # 25 fichiers par domaine : relecture et recherche plus simples
            by_cat: dict[str, list[Item]] = defaultdict(list)
            for it in items:
                by_cat[it.category or "Autres"].append(it)
            for cat, group in sorted(by_cat.items()):
                text, doubt = render_file(f"Catalogue {brand} — {cat}", pole, brand, source,
                                          group, today)
                out.files[f"{SUBDIR}/eduform-{_slug(cat)}.md"] = text
                out.ambiguous[brand] += doubt
        else:
            text, doubt = render_file(f"Catalogue {brand}", pole, brand, source, items, today)
            out.files[f"{SUBDIR}/{slug}.md"] = text
            out.ambiguous[brand] += doubt
    return out


# ------------------------------------------------------------------ écriture
def write(result: Result, root: Path) -> dict[str, int]:
    """Écrit les fichiers modifiés et retire ceux d'un import précédent qui n'existent plus.
    Une marque absente de l'API (panne) ne supprime rien de ses fichiers."""
    root = Path(root)
    target = root / SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    stats = {"ecrits": 0, "inchanges": 0, "retires": 0}
    for rel, text in result.files.items():
        path = root / rel
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            stats["inchanges"] += 1
            continue
        path.write_text(text, encoding="utf-8", newline="\n")
        stats["ecrits"] += 1
    if not result.skipped:  # import complet : on peut retirer les fichiers disparus
        wanted = {root / rel for rel in result.files}
        for old in target.glob("*.md"):
            if old.name != "README.md" and old not in wanted:
                old.unlink()
                stats["retires"] += 1
    return stats


def import_catalog(root: Path, client: httpx.Client | None = None,
                   today: str | None = None, dry_run: bool = False,
                   delay: float = 0.1, with_site: bool = True,
                   min_ratio: float = 0.7) -> tuple[Result, dict]:
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True,
                                    headers={"User-Agent": "ibig-agent/catalogue"})
    try:
        result = build(client, today, delay, with_site)
    finally:
        if own:
            client.close()
    if not result.files:
        raise CatalogError("aucun produit reçu : rien n'a été écrit")
    # Garde contre une API en panne partielle ou vidée : un catalogue très réduit n'écrase pas
    # le précédent (l'agent garde ses données, l'anomalie est signalée)
    before = sum(p.read_text(encoding="utf-8").count("\n### ")
                 for p in (Path(root) / SUBDIR).glob("*.md")) if (Path(root) / SUBDIR).is_dir() else 0
    if before and result.total < before * min_ratio:
        raise CatalogError(f"catalogue reçu anormalement réduit ({result.total} produits contre "
                           f"{before} déjà connus) : rien n'a été écrit")
    return result, ({} if dry_run else write(result, root))
