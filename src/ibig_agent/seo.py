"""Référencement (SEO) de tous les sites du groupe : audit en lecture seule, note sur 100, corrections.

Le service lit les pages PUBLIQUES de chaque site (accueil, quelques pages du plan du site,
robots.txt, sitemap.xml) comme le ferait un moteur de recherche, repère ce qui nuit au
référencement et propose la correction exacte. Il ne modifie jamais un site : les corrections
passent par une mission validée par un humain.

Limite honnête : aucun outil ne peut garantir la première place sur Google. Ce module règle la
partie qui dépend de nous (technique, contenu, structure) ; la position réelle se suit à part,
par exemple dans Google Search Console.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .configstore import service_value, set_service_value
from .db import EcoAsset, utcnow

PENALTY = {"critical": 25, "warning": 8, "info": 3}
PAGES_PER_SITE = 5
HISTORY = 12
KEY = "seo_{}"
TITLE_RANGE = (30, 65)
DESC_RANGE = (70, 160)
MIN_WORDS = 250
SLOW_MS = 2500
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".xml", ".gz", ".mp4")


META_KEY = "seo_meta_{}"
BRAND = ("IBIG SARL (nom abrégé de INTERMARK BUSINESS INTERNATIONAL GROUP, aussi appelée IBI GROUP SARL) : "
         "ces trois noms peuvent apparaître. Ne jamais écrire « IBIG Group ».")
META_CODES = {"title_absent", "title_longueur", "desc_absente", "desc_longueur"}
PRIVATE_MARK = "seo:non"   # dans les notes d'un actif : espace privé, volontairement non indexé


def is_private(asset) -> bool:
    return PRIVATE_MARK in (getattr(asset, "notes", "") or "").lower()


@dataclass
class Issue:
    code: str
    severity: str          # critical | warning | info
    page: str
    problem: str
    fix: str


class _Page(HTMLParser):
    """Relève ce qu'un moteur de recherche lit dans une page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = 0
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.lang = ""
        self.images = 0
        self.no_alt = 0
        self.ldjson = 0
        self.links: list[str] = []
        self.words = 0
        self.excerpt = ""
        self._in: str | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag in ("title", "h1"):
            self._in = tag
            if tag == "h1":
                self.h1 += 1
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            self.canonical = a.get("href", "")
        elif tag == "img":
            self.images += 1
            if not a.get("alt", "").strip():
                self.no_alt += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "script":
            self._skip += 1
            if "ld+json" in a.get("type", "").lower():
                self.ldjson += 1
        elif tag == "style":
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("title", "h1"):
            self._in = None
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        if not self._skip:
            self.words += len(data.split())
            if len(self.excerpt) < 900 and data.strip():
                self.excerpt += " " + " ".join(data.split())


def analyze_page(html: str, url: str) -> tuple[list[Issue], list[str]]:
    """Problèmes de référencement d'une page + ses liens (pour explorer plus loin)."""
    p = _Page()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001, S110 — du HTML cassé ne doit jamais arrêter l'audit
        pass
    issues: list[Issue] = []

    def add(code, sev, problem, fix):
        issues.append(Issue(code, sev, url, problem, fix))

    title = " ".join(p.title.split())
    desc = p.meta.get("description", "").strip()
    if "noindex" in p.meta.get("robots", "").lower():
        add("noindex", "critical", "La page demande aux moteurs de ne PAS l'indexer (noindex).",
            "Retirer `noindex` de la balise `<meta name=\"robots\">` si la page doit être trouvée sur Google.")
    if not title:
        add("title_absent", "critical", "Aucun titre de page.",
            "Ajouter une balise <title> de 30 à 65 caractères avec le mot-clé principal en premier.")
    elif not TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1]:
        add("title_longueur", "warning", f"Titre de {len(title)} caractères (idéal 30 à 65).",
            "Réécrire le titre : mot-clé principal + nom de la marque, sans dépasser 65 caractères.")
    if not desc:
        add("desc_absente", "warning", "Aucune description (meta description).",
            "Ajouter une description de 70 à 160 caractères qui donne envie de cliquer.")
    elif not DESC_RANGE[0] <= len(desc) <= DESC_RANGE[1]:
        add("desc_longueur", "info", f"Description de {len(desc)} caractères (idéal 70 à 160).",
            "Ajuster la description pour qu'elle ne soit ni coupée ni trop courte.")
    if p.h1 == 0:
        add("h1_absent", "warning", "Aucun titre principal (H1).",
            "Ajouter un seul H1 contenant le mot-clé principal de la page.")
    elif p.h1 > 1:
        add("h1_multiple", "info", f"{p.h1} titres H1 sur la même page.", "Garder un seul H1 par page.")
    if not p.canonical:
        add("canonical", "info", "Pas d'adresse canonique.",
            "Ajouter `<link rel=\"canonical\" href=\"…\">` pour éviter les doublons.")
    if not p.lang:
        add("lang", "info", "Langue de la page non déclarée.", "Ajouter `lang=\"fr\"` sur la balise <html>.")
    if "viewport" not in p.meta:
        add("viewport", "warning", "Page non adaptée au téléphone (pas de balise viewport).",
            "Ajouter `<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">` : Google "
            "classe d'abord la version mobile.")
    if not (p.meta.get("og:title") and p.meta.get("og:image")):
        add("opengraph", "info", "Aperçu de partage incomplet (Open Graph).",
            "Ajouter og:title, og:description et og:image pour de beaux aperçus sur WhatsApp et Facebook.")
    if p.images and p.no_alt / p.images > 0.3:
        add("images_alt", "warning", f"{p.no_alt} image(s) sur {p.images} sans texte alternatif.",
            "Ajouter un attribut alt décrivant chaque image (aide le référencement et l'accessibilité).")
    if not p.ldjson:
        add("donnees_structurees", "info", "Aucune donnée structurée (schema.org).",
            "Ajouter un bloc JSON-LD « Organization » (et « Product » ou « Article » selon la page).")
    if p.words < MIN_WORDS:
        add("contenu_court", "warning", f"Contenu très court ({p.words} mots).",
            f"Étoffer la page à au moins {MIN_WORDS} mots utiles (services, preuves, questions fréquentes).")
    return issues, p.links


def score(issues: list[Issue]) -> int:
    """100 moins une pénalité par point ; un même problème répété sur plusieurs pages compte une fois par page
    mais ne fait jamais perdre plus de 40 points au total pour un seul code."""
    per_code: dict[str, int] = {}
    for i in issues:
        per_code[i.code] = per_code.get(i.code, 0) + PENALTY[i.severity]
    return max(0, 100 - sum(min(v, 40) for v in per_code.values()))


def grade(value: int) -> str:
    return "excellent" if value >= 90 else "bon" if value >= 75 else "moyen" if value >= 55 else "faible"


class SeoService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings,
                 assets: Callable[[], list[EcoAsset]], client: httpx.Client | None = None,
                 llm=None) -> None:
        self.llm = llm
        self._sessions = sessions
        self.settings = settings
        self._assets = assets
        self._client = client

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(
            timeout=12, follow_redirects=True,
            headers={"User-Agent": "IBIG-SEO/1.0 (lecture seule)"})

    def _get(self, client: httpx.Client, url: str) -> tuple[int, str, int]:
        started = time.monotonic()
        try:
            r = client.get(url)
        except httpx.HTTPError:
            return 0, "", int((time.monotonic() - started) * 1000)
        return r.status_code, r.text, int((time.monotonic() - started) * 1000)

    # ------------------------------------------------------------------ audit
    def audit(self, asset: EcoAsset, client: httpx.Client | None = None) -> dict:
        client = client or self._http()
        base = asset.url.rstrip("/")
        host = urlparse(base).netloc
        issues: list[Issue] = []
        status, html, ms = self._get(client, base + "/")
        if status == 0 or status >= 400 or not html:
            issues.append(Issue("inaccessible", "critical", base + "/",
                                f"La page d'accueil ne répond pas correctement (HTTP {status or 'aucune réponse'}).",
                                "Rétablir le site d'abord : un site inaccessible ne peut pas être référencé."))
            return self._save(asset, issues, [], ms)
        if ms > SLOW_MS:
            issues.append(Issue("lenteur", "warning", base + "/", f"Accueil lent ({ms} ms).",
                                "Activer le cache, compresser les images : la vitesse compte pour Google."))
        if not base.startswith("https://"):
            issues.append(Issue("https", "critical", base + "/", "Le site n'est pas en HTTPS.",
                                "Installer un certificat et rediriger tout le trafic vers https://."))
        page_issues, links = analyze_page(html, base + "/")
        issues += page_issues

        status, txt, _ = self._get(client, base + "/robots.txt")
        sitemap_urls: list[str] = []
        if status != 200 or not txt.strip():
            issues.append(Issue("robots", "info", base + "/robots.txt", "Pas de fichier robots.txt.",
                                "Créer `robots.txt` avec `User-agent: *`, `Allow: /` et la ligne `Sitemap: …`."))
        else:
            if (re.search(r"(?im)^\s*disallow:\s*/\s*$", txt)
                    and re.search(r"(?im)^\s*user-agent:\s*\*", txt)):
                issues.append(Issue("robots_bloque", "critical", base + "/robots.txt",
                                    "robots.txt interdit TOUT le site aux moteurs de recherche.",
                                    "Remplacer `Disallow: /` par `Allow: /` (sauf zones privées)."))
            sitemap_urls = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", txt)
            if not sitemap_urls:
                issues.append(Issue("robots_sitemap", "info", base + "/robots.txt",
                                    "robots.txt ne déclare pas le plan du site.",
                                    f"Ajouter la ligne `Sitemap: {base}/sitemap.xml`."))
        sm_url = sitemap_urls[0] if sitemap_urls else base + "/sitemap.xml"
        status, xml, _ = self._get(client, sm_url)
        candidates: list[str] = []
        if status != 200 or ("<urlset" not in xml and "<sitemapindex" not in xml):
            issues.append(Issue("sitemap", "warning", sm_url, "Pas de plan du site (sitemap.xml) valide.",
                                "Générer un sitemap.xml listant toutes les pages et le déclarer dans robots.txt "
                                "et dans Google Search Console."))
        else:
            candidates += [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
                           if urlparse(u).netloc == host and not u.lower().endswith((".xml", ".gz"))]
        for href in links:                                  # à défaut, on suit les liens internes
            u = urljoin(base + "/", href.split("#")[0])
            if urlparse(u).netloc == host and u.startswith("http") and not u.lower().endswith(SKIP_EXT):
                candidates.append(u)
        seen = [base + "/"]
        for u in candidates:
            if u.rstrip("/") not in {s.rstrip("/") for s in seen}:
                seen.append(u)
        for u in seen[1:PAGES_PER_SITE]:
            st, h, _ = self._get(client, u)
            if st == 0 or st >= 400:
                issues.append(Issue("page_cassee", "warning", u,
                                    f"Page du site en erreur (HTTP {st or 'aucune réponse'}).",
                                    "Corriger ou supprimer le lien, ou rediriger la page vers la bonne adresse."))
            elif h:
                issues += analyze_page(h, u)[0]
        return self._save(asset, issues, seen[:PAGES_PER_SITE], ms)

    def _save(self, asset: EcoAsset, issues: list[Issue], pages: list[str], ms: int) -> dict:
        prev = self.result(asset.id) or {}
        value = score(issues)
        now = utcnow().isoformat(timespec="minutes")
        data = {"at": now, "score": value, "grade": grade(value), "ms": ms, "pages": pages,
                "issues": [asdict(i) for i in issues],
                "history": (prev.get("history", []) + [{"at": now, "score": value}])[-HISTORY:],
                "previous": prev.get("score")}
        set_service_value(self._sessions, self.settings, KEY.format(asset.id), json.dumps(data), "agent")
        return data

    def audit_site(self, asset_id: int) -> dict:
        asset = next((a for a in self._assets() if a.id == asset_id), None)
        if asset is None:
            raise ValueError("Site introuvable")
        return self.audit(asset)

    def run(self) -> dict:
        """Audit de tous les sites suivis (en parallèle, sans jamais rien modifier chez eux)."""
        sites = [a for a in self._assets() if a.active and a.kind == "site" and a.url.startswith("http")
                 and not is_private(a)]
        client = self._http()
        with ThreadPoolExecutor(4) as ex:
            res = list(ex.map(lambda a: self.audit(a, client), sites))
        return {"sites": len(sites),
                "moyenne": round(sum(r["score"] for r in res) / len(res)) if res else None}

    # ------------------------------------------------------------------ lecture
    def result(self, asset_id: int) -> dict | None:
        raw = service_value(self._sessions, self.settings, KEY.format(asset_id))
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def overview(self) -> list[dict]:
        """Tous les sites, du moins bien référencé au mieux : ce qu'il faut traiter d'abord."""
        rows = []
        for a in self._assets():
            if a.kind != "site" or is_private(a):
                continue
            r = self.result(a.id)
            sev = {"critical": 0, "warning": 0, "info": 0}
            for i in (r or {}).get("issues", []):
                sev[i["severity"]] += 1
            rows.append({"asset": a, "result": r, "sev": sev})
        rows.sort(key=lambda x: (x["result"] is None, x["result"]["score"] if x["result"] else 0))
        return rows

    def fix_plan(self, asset_id: int) -> tuple[str, str, list[tuple[str, str]]]:
        r = self.result(asset_id)
        if not r or not r["issues"]:
            raise ValueError("Rien à corriger : lancez d'abord un audit")
        name = next((a.name for a in self._assets() if a.id == asset_id), "site")
        order = {"critical": 0, "warning": 1, "info": 2}
        grouped: dict[str, Issue] = {}
        for i in sorted((Issue(**x) for x in r["issues"]), key=lambda x: order[x.severity]):
            grouped.setdefault(i.code, i)
        steps = [(i.problem[:80], f"{i.fix}\nPage : {i.page}") for i in list(grouped.values())[:9]]
        ok = [m for m in self.meta_proposals(asset_id) if m["status"] == "valide"]
        if ok:
            snippets = "\n\n".join(f"Page : {m['page']}\n<title>{m['title']}</title>\n"
                                    f"<meta name=\"description\" content=\"{m['description']}\">" for m in ok)
            steps.insert(0, (f"Appliquer les {len(ok)} titre(s) et description(s) validés",
                             "Remplacer les balises de chaque page par celles-ci (déjà validées par la direction) :"
                             "\n\n" + snippets))
        return (f"Améliorer le référencement de {name}",
                f"Faire passer {name} de {r['score']}/100 à 90/100 en corrigeant les points ci-dessous.", steps)


    # ------------------------------------------------------------------ titres et descriptions
    def meta_proposals(self, asset_id: int) -> list[dict]:
        raw = service_value(self._sessions, self.settings, META_KEY.format(asset_id))
        try:
            return json.loads(raw) if raw else []
        except ValueError:
            return []

    def propose_meta(self, asset_id: int, by: str) -> list[dict]:
        """L'IA rédige un titre et une description pour chaque page à corriger. Ce sont des PROPOSITIONS :
        rien n'est publié, un humain valide chacune."""
        if self.llm is None:
            raise ValueError("L'IA n'est pas configurée")
        r = self.result(asset_id)
        asset = next((a for a in self._assets() if a.id == asset_id), None)
        if asset is None or r is None:
            raise ValueError("Lancez d'abord un audit de ce site")
        pages = list(dict.fromkeys(i["page"] for i in r["issues"] if i["code"] in META_CODES))[:PAGES_PER_SITE]
        if not pages:
            raise ValueError("Titres et descriptions déjà corrects")
        client, out = self._http(), []
        for url in pages:
            _status, html, _ms = self._get(client, url)
            page = _Page()
            try:
                page.feed(html)
            except Exception:  # noqa: BLE001, S110 — du HTML cassé ne doit jamais arrêter la rédaction
                pass
            facts = (f"Site : {asset.name} ({asset.url})\nPôle : {asset.pole or 'groupe'}\nPage : {url}\n"
                     f"Titre actuel : {' '.join(page.title.split()) or '(aucun)'}\n"
                     f"Description actuelle : {page.meta.get('description', '(aucune)')}\n"
                     f"Texte de la page : {page.excerpt.strip()[:800]}")
            try:
                text = self.llm.write(
                    "seo.meta",
                    "Tu rédiges le référencement d'une page pour IBIG (Abidjan). Réponds UNIQUEMENT par un JSON "
                    '{"title": "...", "description": "..."} en français. Titre : 40 à 60 caractères, mot-clé '
                    "principal en premier puis la marque. Description : 120 à 155 caractères, concrète, donne "
                    "envie de cliquer. N'utilise que les faits du texte fourni : aucun prix, aucun chiffre, "
                    f"aucune promesse inventés. Marque : {BRAND}", facts, 300)
                data = json.loads(text[text.index("{"):text.rindex("}") + 1])
                title, desc = " ".join(str(data["title"]).split()), " ".join(str(data["description"]).split())
            except Exception:  # noqa: BLE001, S112 — une page sans proposition valable est simplement ignorée
                continue
            if "ibig group" in (title + desc).lower():
                continue
            if TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1] and DESC_RANGE[0] <= len(desc) <= DESC_RANGE[1]:
                out.append({"page": url, "title": title, "description": desc, "status": "propose"})
        set_service_value(self._sessions, self.settings, META_KEY.format(asset_id), json.dumps(out), by)
        return out

    def decide_meta(self, asset_id: int, index: int, approve: bool, by: str) -> None:
        items = self.meta_proposals(asset_id)
        if not 0 <= index < len(items):
            raise ValueError("Proposition introuvable")
        items[index]["status"] = "valide" if approve else "rejete"
        items[index]["by"] = by
        set_service_value(self._sessions, self.settings, META_KEY.format(asset_id), json.dumps(items), by)
