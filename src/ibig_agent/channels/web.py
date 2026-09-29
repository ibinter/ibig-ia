"""Connecteurs des sites web (section 11) : l'agent dépose des articles EN BROUILLON.

* WordPress : API REST, compte « Auteur » dédié, mot de passe d'application ;
* PHP maison : module d'entrée sécurisé (integrations/php/ibig-article-inbox.php),
  requête signée HMAC-SHA256 avec horodatage (anti-rejeu) ;
* statique : fichier HTML livré dans un dossier, intégré à la main.

Dans tous les cas, un humain relit et met en ligne sur le site : l'agent ne publie jamais.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

import httpx

from ..config import Site

# ------------------------------------------------------------------ nettoyage HTML
ALLOWED_TAGS = {"h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "em", "b", "i", "a",
                "blockquote", "br", "table", "thead", "tbody", "tr", "th", "td"}
VOID_TAGS = {"br"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        extra = ""
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not re.match(r"^https?://", href.strip(), re.IGNORECASE):
                return  # lien absent ou dangereux (javascript:, data:…) : balise ignorée
            extra = f' href="{html.escape(href.strip(), quote=True)}"'
        self.out.append(f"<{tag}{extra}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out).strip()


def sanitize_html(raw: str) -> str:
    """Ne garde que les balises de mise en forme d'un article ; liens http(s) uniquement."""
    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    return parser.result()


def html_to_text(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def slugify(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80] or "article"


# ------------------------------------------------------------------ connecteurs
class WebConnector(Protocol):
    site: Site

    def create_draft(self, article: dict) -> dict: ...


class WordPressConnector:
    def __init__(self, site: Site, client: httpx.Client | None = None) -> None:
        self.site = site
        self.client = client or httpx.Client(timeout=30)

    def create_draft(self, article: dict) -> dict:
        resp = self.client.post(
            f"{self.site.url.rstrip('/')}/wp-json/wp/v2/posts",
            auth=(self.site.wp_user, self.site.secret()),
            json={
                "status": "draft",  # jamais « publish » : un humain met en ligne
                "title": article["titre"],
                "content": article["contenu_html"],
                "excerpt": article.get("meta_description", ""),
                "slug": article["slug"],
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"WordPress {resp.status_code} : {resp.text[:300]}")
        data = resp.json()
        if data.get("status") != "draft":
            raise RuntimeError(f"Statut inattendu renvoyé par WordPress : {data.get('status')}")
        return {"mode": "wordpress", "id": data.get("id"), "lien": data.get("link", "")}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class PhpEndpointConnector:
    def __init__(self, site: Site, client: httpx.Client | None = None) -> None:
        self.site = site
        self.client = client or httpx.Client(timeout=30)

    def create_draft(self, article: dict) -> dict:
        body = json.dumps({
            "titre": article["titre"],
            "slug": article["slug"],
            "contenu_html": article["contenu_html"],
            "meta_description": article.get("meta_description", ""),
            "mot_cle": article.get("mot_cle", ""),
        }, ensure_ascii=False).encode()
        ts = str(int(time.time()))
        resp = self.client.post(
            self.site.endpoint_url, content=body,
            headers={"Content-Type": "application/json", "X-IBIG-Timestamp": ts,
                     "X-IBIG-Signature": sign(self.site.secret(), ts, body)},
        )
        if resp.status_code != 201:
            raise RuntimeError(f"Module PHP {resp.status_code} : {resp.text[:300]}")
        return {"mode": "php", **resp.json()}


HTML_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{titre}</title>
<meta name="description" content="{description}">
<!-- Brouillon préparé par l'agent IA IBIG le {date} — à relire avant mise en ligne. -->
</head>
<body>
<article>
<h1>{titre}</h1>
{contenu}
</article>
</body>
</html>
"""


class StaticExportConnector:
    def __init__(self, site: Site) -> None:
        self.site = site

    def create_draft(self, article: dict) -> dict:
        folder = Path(self.site.export_dir) / slugify(self.site.nom)
        folder.mkdir(parents=True, exist_ok=True)
        today = datetime.now(UTC).date()
        path = folder / f"{today:%Y-%m-%d}-{slugify(article['slug'])}.html"
        path.write_text(HTML_PAGE.format(
            titre=html.escape(article["titre"]),
            description=html.escape(article.get("meta_description", ""), quote=True),
            date=f"{today:%d/%m/%Y}",
            contenu=article["contenu_html"],
        ), encoding="utf-8")
        return {"mode": "statique", "fichier": str(path)}


def web_connector_for(site: Site) -> WebConnector | None:
    if site.technologie == "wordpress":
        return WordPressConnector(site)
    if site.technologie == "php":
        return PhpEndpointConnector(site)
    if site.technologie == "statique":
        return StaticExportConnector(site)
    return None
