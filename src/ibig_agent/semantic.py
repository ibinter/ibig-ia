"""Recherche par le sens dans la base de connaissances (section 14 : PostgreSQL + pgvector).

* Chaque passage de la base (section ## ou ###) est converti en vecteur par Voyage AI,
  le service d'« embeddings » recommandé par Anthropic (Claude n'en produit pas). La clé
  est saisie au tableau de bord (menu Services) et chiffrée dans le coffre.
* Les vecteurs sont rangés dans PostgreSQL (colonne pgvector, recherche par l'opérateur
  `<=>`) ; sans pgvector (SQLite, essais), le calcul se fait en Python.
* La recherche est hybride : mots-clés + sens. Sans clé, ou si Voyage ne répond pas,
  l'agent retombe sur les mots-clés seuls : rien ne s'arrête.
* Seul le texte des documents de la base est envoyé à Voyage (pas les messages clients) ;
  une question n'y part que pour être comparée à la base.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import OrderedDict

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import AIUsage, KbEmbedding, utcnow

log = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
MODEL = "voyage-3.5"
DIM = 1024
PRICE_PER_M_TOKENS = 0.06  # USD, voyage-3.5 (à revérifier sur voyageai.com/pricing)
BATCH = 64


class SemanticError(RuntimeError):
    pass


class VoyageClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    def embed(self, texts: list[str], input_type: str) -> tuple[list[list[float]], int]:
        resp = self.client.post(VOYAGE_URL, headers={"Authorization": f"Bearer {self.api_key}"},
                                json={"input": texts, "model": MODEL, "input_type": input_type,
                                      "output_dimension": DIM})
        if resp.status_code != 200:
            raise SemanticError(f"Voyage {resp.status_code} : {resp.text[:200]}")
        data = resp.json()
        rows = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return ([r["embedding"] for r in rows],
                int((data.get("usage") or {}).get("total_tokens", 0)))


def passage_text(p) -> str:
    return f"{p.doc.titre} — {p.heading}\n{p.text}"[:6000]


def _hash(s: str) -> str:
    return hashlib.sha256(f"{MODEL}:{s}".encode()).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


class SemanticIndex:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings,
                 client_factory=None) -> None:
        self._sessions, self.settings = sessions, settings
        self.client_factory = client_factory or VoyageClient
        self._vectors: dict[str, list[float]] | None = None
        self._queries: OrderedDict[str, list[float]] = OrderedDict()
        self.last_error = ""
        self._pgvector: bool | None = None

    # ------------------------------------------------------------ état
    def api_key(self) -> str:
        from .configstore import service_value

        return service_value(self._sessions, self.settings, "voyage_api_key")

    def enabled(self) -> bool:
        return bool(self.api_key())

    def pgvector(self) -> bool:
        """Colonne pgvector présente (PostgreSQL avec l'extension) ; vérifié une fois."""
        if self._pgvector is None:
            with self._sessions() as s:
                self._pgvector = s.get_bind().dialect.name == "postgresql" and bool(
                    s.execute(text("SELECT 1 FROM information_schema.columns WHERE "
                                   "table_name = 'kb_embeddings' AND column_name = 'vec'"))
                    .scalar())
        return self._pgvector

    def count(self) -> int:
        with self._sessions() as s:
            return len(s.scalars(select(KbEmbedding.id)).all())

    # ------------------------------------------------------------ indexation
    def sync(self, kb) -> dict:
        """Met l'index à jour : seuls les passages nouveaux ou modifiés partent chez Voyage."""
        key = self.api_key()
        if not key:
            return {"actif": False}
        wanted = {p.id: passage_text(p) for p in kb.passages
                  if p.doc.type != "publication"}
        with self._sessions() as s:
            have = {r.id: r.content_hash for r in s.scalars(select(KbEmbedding)).all()}
            stale = [i for i in have if i not in wanted]
            for i in stale:
                s.delete(s.get(KbEmbedding, i))
            s.commit()
        todo = [(i, t) for i, t in wanted.items() if have.get(i) != _hash(t)]
        client = self.client_factory(key)
        use_pg = self.pgvector()
        tokens = 0
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            vectors, used = client.embed([t for _, t in chunk], "document")
            tokens += used
            with self._sessions() as s:
                for (pid, t), vec in zip(chunk, vectors, strict=True):
                    s.merge(KbEmbedding(id=pid, content_hash=_hash(t), model=MODEL,
                                        embedding=vec, updated_at=utcnow()))
                s.flush()
                if use_pg:
                    for (pid, _), vec in zip(chunk, vectors, strict=True):
                        s.execute(text("UPDATE kb_embeddings SET vec = CAST(:v AS vector) "
                                       "WHERE id = :i"), {"v": _vec_literal(vec), "i": pid})
                s.commit()
        self._record(tokens, "kb.index")
        self._vectors = None
        return {"actif": True, "indexes": len(todo), "retires": len(stale),
                "total": len(wanted), "pgvector": use_pg}

    def _record(self, tokens: int, purpose: str) -> None:
        if not tokens:
            return
        with self._sessions() as s:
            s.add(AIUsage(model=MODEL, purpose=purpose, input_tokens=tokens, output_tokens=0,
                          cost_usd=tokens * PRICE_PER_M_TOKENS / 1_000_000))
            s.commit()

    # ------------------------------------------------------------ recherche
    def _query_vector(self, query: str) -> list[float]:
        q = " ".join(query.split())[:2000]
        if q in self._queries:
            self._queries.move_to_end(q)
            return self._queries[q]
        vectors, used = self.client_factory(self.api_key()).embed([q], "query")
        self._record(used, "kb.query")
        self._queries[q] = vectors[0]
        if len(self._queries) > 200:
            self._queries.popitem(last=False)
        return vectors[0]

    def scores(self, query: str, limit: int = 60) -> dict[str, float]:
        """Similarité (0 à 1) des passages les plus proches de la question ; vide si
        l'index n'est pas actif ou si Voyage ne répond pas."""
        if not query.strip() or not self.enabled():
            return {}
        try:
            qv = self._query_vector(query)
            if self.pgvector():
                with self._sessions() as s:
                    rows = s.execute(text(
                        "SELECT id, 1 - (vec <=> CAST(:q AS vector)) FROM kb_embeddings "
                        "WHERE vec IS NOT NULL ORDER BY vec <=> CAST(:q AS vector) LIMIT :n"),
                        {"q": _vec_literal(qv), "n": limit}).all()
                return {i: float(sc) for i, sc in rows}
            if self._vectors is None:
                with self._sessions() as s:
                    self._vectors = {r.id: r.embedding
                                     for r in s.scalars(select(KbEmbedding)).all()}
            ranked = sorted(((cosine(qv, v), i) for i, v in self._vectors.items()),
                            reverse=True)[:limit]
            return {i: sc for sc, i in ranked}
        except Exception as exc:  # noqa: BLE001 — repli sur les mots-clés
            self.last_error = str(exc)[:200]
            log.warning("Recherche par le sens indisponible : %s", exc)
            return {}
