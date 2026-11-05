"""Publication centrale d'articles : on rédige une fois, l'agent publie sur les sites choisis.

Format du texte (simple, compris par les sites) : `## titre`, `### sous-titre`, `- liste`, paragraphes.
Un article n'est publié que s'il a été validé (« prêt ») ; chaque publication est tracée pour pouvoir être
retirée. Les noms de l'entreprise sont contrôlés (jamais « IBIG Group »).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .configstore import service_value, set_service_value
from .db import Article, ArticlePublication, utcnow

BRAND = ("IBIG SARL (nom abrégé de INTERMARK BUSINESS INTERNATIONAL GROUP, aussi appelée IBI GROUP SARL) : "
         "ces trois noms peuvent apparaître. Ne jamais écrire « IBIG Group ».")
BANNED = re.compile(r"ibig\s+group", re.IGNORECASE)
DIGITAL_SECRET = "connector_digital_url"
INTERMARK_SECRET = "connector_intermark_secret"


def slugify(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:100] or "article"


def check_names(*parts: str) -> None:
    if any(BANNED.search(p or "") for p in parts):
        raise ValueError("Le nom « IBIG Group » est interdit : écrire IBIG SARL, "
                         "INTERMARK BUSINESS INTERNATIONAL GROUP ou IBI GROUP SARL")


def to_html(text_: str) -> str:
    """Texte simple (## titre, ### sous-titre, - liste, **gras**) -> HTML sûr pour les sites qui affichent du HTML."""
    def inline(s: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", html.escape(s))
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text_.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("### "):
            flush()
            out.append(f"<h3>{inline(s[4:])}</h3>")
        elif s.startswith("## "):
            flush()
            out.append(f"<h2>{inline(s[3:])}</h2>")
        elif s.startswith("- "):
            if para:
                flush()
            items.append(s[2:])
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


@dataclass
class Published:
    external_id: str
    url: str


class DigitalConnector:
    """IBIG DIGITAL : écrit dans la table blog_posts de sa base (statut published / archived)."""

    key = "DIGITAL"
    label = "IBIG DIGITAL (ibig-digital.com)"
    site = "https://ibig-digital.com"

    def __init__(self, get_url: Callable[[], str]):
        self._get_url = get_url

    def ready(self) -> bool:
        return bool(self._get_url())

    def _engine(self):
        url = self._get_url()
        if not url:
            raise ValueError("Connexion à la base d'IBIG DIGITAL non configurée")
        return create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1), pool_pre_ping=True)

    def publish(self, article: Article) -> Published:
        eng = self._engine()
        try:
            with eng.begin() as conn:
                cat = None
                if article.category:
                    cat = conn.execute(text("select id from blog_categories where name = :n limit 1"),
                                       {"n": article.category}).scalar()
                    if cat is None:
                        cat = conn.execute(text(
                            "insert into blog_categories (name, slug, is_active) values (:n, :s, true) returning id"),
                            {"n": article.category, "s": slugify(article.category)}).scalar()
                slug, n = article.slug, 1
                while conn.execute(text("select 1 from blog_posts where slug = :s"), {"s": slug}).first():
                    n += 1
                    slug = f"{article.slug}-{n}"
                new_id = conn.execute(text(
                    "insert into blog_posts (category_id, title, slug, excerpt, content, cover_image, author, "
                    "seo_title, seo_description, og_image, status, published_at) "
                    "values (:cat, :t, :s, :e, :c, :img, 'IBIG DIGITAL', :st, :sd, :img, "
                    "cast('published' as blog_status_enum), now()) returning id"),
                    {"cat": cat, "t": article.title, "s": slug, "e": article.excerpt, "c": article.content,
                     "img": article.cover_image or None, "st": article.seo_title or article.title,
                     "sd": article.seo_description or article.excerpt}).scalar()
        finally:
            eng.dispose()
        return Published(str(new_id), f"{self.site}/blog/{slug}")

    def unpublish(self, external_id: str) -> None:
        eng = self._engine()
        try:
            with eng.begin() as conn:
                conn.execute(text("update blog_posts set status = cast('archived' as blog_status_enum), "
                                  "updated_at = now() where id = cast(:i as uuid)"), {"i": external_id})
        finally:
            eng.dispose()


class IntermarkConnector:
    """INTERMARK BUSINESS : appelle la porte d'entrée signée du site (api/publier-article.php)."""

    key = "INTERMARK"
    label = "INTERMARK BUSINESS (intermark-business.com)"
    endpoint = "https://intermark-business.com/api/publier-article.php"

    def __init__(self, get_secret: Callable[[], str], client: httpx.Client | None = None):
        self._get_secret = get_secret
        self._client = client

    def ready(self) -> bool:
        return bool(self._get_secret())

    def _call(self, payload: dict) -> dict:
        secret = self._get_secret()
        if not secret:
            raise ValueError("Clé de publication d'INTERMARK BUSINESS non configurée")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Ibig-Timestamp": ts, "X-Ibig-Signature": sig,
                   "User-Agent": "IBIG-Agent/1.0"}
        client = self._client or httpx.Client(timeout=30)
        resp = client.post(self.endpoint, content=body, headers=headers)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or not data.get("ok"):
            raise ValueError(f"Le site a refusé ({resp.status_code}) : {data.get('error', 'réponse inattendue')}")
        return data

    def ping(self) -> bool:
        return bool(self._call({"action": "ping"}).get("ok"))

    def publish(self, article: Article) -> Published:
        data = self._call({"action": "publish", "titre": article.title, "slug": article.slug,
                           "resume": article.excerpt, "contenu": to_html(article.content),
                           "image": article.cover_image, "pole": "", "meta_title": article.seo_title or article.title,
                           "meta_description": article.seo_description or article.excerpt})
        return Published(str(data["id"]), str(data["url"]))

    def unpublish(self, external_id: str) -> None:
        self._call({"action": "unpublish", "id": int(external_id)})


class ArticleService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, llm=None, connectors: list | None = None):
        self._sessions = sessions
        self._settings = settings
        self._llm = llm
        self._connectors = connectors if connectors is not None else [
            DigitalConnector(lambda: service_value(sessions, settings, DIGITAL_SECRET)),
            IntermarkConnector(lambda: service_value(sessions, settings, INTERMARK_SECRET))]

    # ---------------------------------------------------------------- configuration
    def set_digital_url(self, url: str, by: str) -> None:
        set_service_value(self._sessions, self._settings, DIGITAL_SECRET, url.strip(), by, secret=True)

    def set_intermark_secret(self, secret: str, by: str) -> None:
        set_service_value(self._sessions, self._settings, INTERMARK_SECRET, secret.strip(), by, secret=True)

    def targets(self) -> list[dict]:
        return [{"key": c.key, "label": c.label, "ready": c.ready()} for c in self._connectors]

    # ---------------------------------------------------------------- brouillons
    def create(self, title: str, content: str, by: str, excerpt: str = "", category: str = "",
               cover_image: str = "", seo_title: str = "", seo_description: str = "") -> int:
        title = title.strip()
        if not title:
            raise ValueError("Le titre est obligatoire")
        check_names(title, content, excerpt, seo_title, seo_description)
        with self._sessions() as s:
            row = Article(title=title[:300], slug=slugify(title), excerpt=excerpt.strip(), content=content.strip(),
                          category=category.strip()[:100], cover_image=cover_image.strip()[:500],
                          seo_title=seo_title.strip()[:300], seo_description=seo_description.strip()[:400],
                          created_by=by)
            s.add(row)
            s.commit()
            return row.id

    def update(self, article_id: int, title: str, content: str, excerpt: str, category: str, cover_image: str,
               seo_title: str, seo_description: str) -> None:
        check_names(title, content, excerpt, seo_title, seo_description)
        with self._sessions() as s:
            row = s.get(Article, article_id)
            if row is None:
                raise ValueError("Article introuvable")
            if not title.strip():
                raise ValueError("Le titre est obligatoire")
            row.title, row.content, row.excerpt = title.strip()[:300], content.strip(), excerpt.strip()
            row.category, row.cover_image = category.strip()[:100], cover_image.strip()[:500]
            row.seo_title, row.seo_description = seo_title.strip()[:300], seo_description.strip()[:400]
            if row.status == "pret":
                row.status = "brouillon"            # toute modification demande une nouvelle validation
            row.updated_at = utcnow()
            s.commit()

    def draft_with_ai(self, topic: str, key_points: str, by: str) -> int:
        if self._llm is None:
            raise ValueError("L'assistant de rédaction n'est pas disponible")
        topic = topic.strip()
        if not topic:
            raise ValueError("Indiquez le sujet de l'article")
        system = (f"Tu rédiges des articles de blog en français pour les sites du groupe. {BRAND} "
                  "Réponds UNIQUEMENT par un objet JSON avec les clés title (≤ 65 caractères), excerpt (≤ 160), "
                  "content, seo_title (≤ 60), seo_description (≤ 155), category (un mot ou deux). "
                  "content utilise ce format simple : lignes `## titre`, `### sous-titre`, `- point`, et des "
                  "paragraphes séparés par une ligne vide. 500 à 800 mots, utile, concret, sans promesse chiffrée "
                  "inventée.")
        raw = self._llm.write("article", system, f"Sujet : {topic}\nPoints à couvrir : {key_points}", 4000)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            d = json.loads(match.group(0)) if match else {}
        except ValueError:
            d = {}
        if not d.get("title") or not d.get("content"):
            raise ValueError("La rédaction n'a pas produit un article exploitable : réessayez")
        return self.create(str(d["title"]), str(d["content"]), by, excerpt=str(d.get("excerpt", "")),
                           category=str(d.get("category", "")), seo_title=str(d.get("seo_title", "")),
                           seo_description=str(d.get("seo_description", "")))

    def approve(self, article_id: int) -> None:
        with self._sessions() as s:
            row = s.get(Article, article_id)
            if row is None:
                raise ValueError("Article introuvable")
            if len(row.content.split()) < 80:
                raise ValueError("L'article est trop court pour être publié (80 mots au moins)")
            row.status = "pret" if row.status == "brouillon" else row.status
            row.updated_at = utcnow()
            s.commit()

    # ---------------------------------------------------------------- publication
    def publish(self, article_id: int, targets: list[str], by: str) -> list[dict]:
        by_key = {c.key: c for c in self._connectors}
        with self._sessions() as s:
            art = s.get(Article, article_id)
            if art is None:
                raise ValueError("Article introuvable")
            if art.status not in ("pret", "publie"):
                raise ValueError("Validez l'article (« prêt à publier ») avant de le publier")
            results = []
            for key in targets:
                conn = by_key.get(key)
                if conn is None or not conn.ready():
                    raise ValueError(f"Le site {key} n'est pas relié")
                already = s.scalar(select(ArticlePublication).where(
                    ArticlePublication.article_id == art.id, ArticlePublication.target == key,
                    ArticlePublication.status == "ok"))
                if already:
                    results.append({"target": key, "status": "deja", "url": already.url})
                    continue
                try:
                    pub = conn.publish(art)
                    s.add(ArticlePublication(article_id=art.id, target=key, status="ok", external_id=pub.external_id,
                                             url=pub.url, by=by))
                    art.status = "publie"
                    results.append({"target": key, "status": "ok", "url": pub.url})
                except Exception as exc:  # noqa: BLE001 - l'échec d'un site ne bloque pas les autres
                    s.add(ArticlePublication(article_id=art.id, target=key, status="erreur",
                                             error=f"{type(exc).__name__}: {str(exc)[:240]}", by=by))
                    results.append({"target": key, "status": "erreur", "error": str(exc)[:240]})
            art.updated_at = utcnow()
            s.commit()
        return results

    def unpublish(self, article_id: int, target: str, by: str) -> None:
        conn = next((c for c in self._connectors if c.key == target), None)
        with self._sessions() as s:
            pub = s.scalar(select(ArticlePublication).where(
                ArticlePublication.article_id == article_id, ArticlePublication.target == target,
                ArticlePublication.status == "ok"))
            if pub is None or conn is None:
                raise ValueError("Cet article n'est pas publié sur ce site")
            conn.unpublish(pub.external_id)
            pub.status, pub.by, pub.at = "retire", by, utcnow()
            s.commit()

    # ---------------------------------------------------------------- lecture
    def rows(self) -> list[Article]:
        with self._sessions() as s:
            return list(s.scalars(select(Article).order_by(Article.updated_at.desc())).all())

    def get(self, article_id: int) -> tuple[Article, list[ArticlePublication]] | None:
        with self._sessions() as s:
            art = s.get(Article, article_id)
            if art is None:
                return None
            pubs = list(s.scalars(select(ArticlePublication).where(ArticlePublication.article_id == article_id)
                                  .order_by(ArticlePublication.at.desc())).all())
            return art, pubs
