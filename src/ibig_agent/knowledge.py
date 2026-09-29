"""Base de connaissances : source unique de vérité (section 7).

Un agent ne publie jamais un prix, un contact ou une promesse qui n'y figure pas.
`verify_facts` contrôle chaque contenu produit avant qu'il ne parte en validation.

Cette version lit des fichiers Markdown versionnés (knowledge/). La recherche
sémantique (pgvector) viendra se brancher derrière `search()` sans changer l'API.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Document:
    path: str
    titre: str
    pole: str
    type: str
    body: str
    meta: dict = field(default_factory=dict)


@dataclass
class Passage:
    """Section d'un document (découpage aux intertitres ## et ###), citable par son id."""

    id: str
    doc: Document
    heading: str
    text: str

    @property
    def auto_ok(self) -> bool:
        """Un humain a validé ce document comme source de réponses automatiques."""
        return self.doc.meta.get("reponses_auto") is True


def normalize_quote(text: str) -> str:
    """Pour vérifier une citation : casse, espaces, apostrophes et emphase ignorés."""
    text = text.replace("’", "'").replace("«", '"').replace("»", '"')
    text = re.sub(r"[*_`]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip(" .;:")


@dataclass
class FaqEntry:
    id: str
    pole: str
    question: str
    answer: str


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _strip_accents(text.lower())) if len(t) > 2]


# Mots trop fréquents pour départager des passages (après suppression des accents).
STOPWORDS = {
    "les", "des", "une", "est", "son", "ses", "aux", "avec", "pour", "par", "sur", "dans",
    "que", "qui", "quoi", "quel", "quelle", "quels", "quelles", "comment", "combien",
    "pourquoi", "quand", "ont", "sont", "etre", "avoir", "fait", "faire", "vous", "nous",
    "votre", "vos", "notre", "nos", "leur", "leurs", "cette", "ces", "cet", "mon", "mes",
    "ton", "tes", "plus", "moins", "tres", "bien", "aussi", "mais", "donc", "car", "pas",
    "the", "and", "for", "you", "are", "with", "this", "that", "what", "how", "can", "your",
}


def _keywords(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in STOPWORDS]


def _slug(text: str) -> str:
    return "-".join(_tokens(text))[:60] or "entree"


def _parse(path: Path, root: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    return Document(
        path=str(path.relative_to(root)),
        titre=str(meta.get("titre", path.stem)),
        pole=str(meta.get("pole", "") or ""),
        type=str(meta.get("type", "") or ""),
        body=body.strip(),
        meta=meta,
    )


# --- Extraction des faits sensibles --------------------------------------------------
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s)>\]\"']+", re.IGNORECASE)
_PHONE = re.compile(r"(?:\+|00)?\d[\d .-]{7,}\d")
_PRICE = re.compile(
    r"\d[\d .,]*\s?(?:f\s?cfa|fcfa|xof|cfa|€|eur|euros?|\$|usd|dollars?)(?![a-z])"
    r"|(?:€|\$)\s?\d[\d .,]*",
    re.IGNORECASE,
)
_DATE = re.compile(r"\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4}")
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?%")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _norm_url(u: str) -> str:
    u = re.sub(r"^https?://", "", u.lower()).removeprefix("www.")
    return u.rstrip("/.,;:")


@dataclass
class FactIssue:
    kind: str  # email | url | telephone | prix | pourcentage | formulation_interdite
    value: str

    def __str__(self) -> str:
        return f"{self.kind} : {self.value}"


class KnowledgeBase:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.documents: list[Document] = []
        self.faq: list[FaqEntry] = []
        self.passages: list[Passage] = []
        self.reload()

    def reload(self) -> None:
        self.documents = [
            _parse(p, self.root)
            for p in sorted(self.root.rglob("*.md"))
            if p.name != "README.md" and not p.name.startswith("_")
        ]
        self.faq = []
        for doc in self.documents:
            if doc.type == "faq":
                self.faq.extend(self._faq_entries(doc))
        self.passages = [p for d in self.documents for p in self._split(d)]
        corpus = "\n".join(d.body for d in self.documents)
        self._emails = {e.lower() for e in _EMAIL.findall(corpus)}
        self._urls = {_norm_url(u) for u in _URL.findall(corpus)}
        self._phones = {_digits(p) for p in _PHONE.findall(corpus)}
        self._prices = {_digits(p) for p in _PRICE.findall(corpus)}
        self._percents = {_digits(p) for p in _PERCENT.findall(corpus)}
        self._forbidden = self._forbidden_phrases()

    @staticmethod
    def _faq_entries(doc: Document) -> list[FaqEntry]:
        entries = []
        stem = Path(doc.path).stem
        for block in re.split(r"^##\s+", doc.body, flags=re.MULTILINE)[1:]:
            question, _, answer = block.partition("\n")
            question = question.strip().removeprefix("Q:").strip()
            if answer.strip():
                entries.append(
                    FaqEntry(f"{stem}#{_slug(question)}", doc.pole, question, answer.strip())
                )
        return entries

    @staticmethod
    def _split(doc: Document) -> list[Passage]:
        parts = re.split(r"^(#{2,3}\s+.+)$", doc.body, flags=re.MULTILINE)
        passages, heading = [], doc.titre
        if parts[0].strip():
            passages.append((heading, parts[0].strip()))
        for i in range(1, len(parts), 2):
            heading = parts[i].lstrip("#").strip()
            if parts[i + 1].strip():
                passages.append((heading, parts[i + 1].strip()))
        return [Passage(f"{doc.path}#{n}", doc, h, t) for n, (h, t) in enumerate(passages, 1)]

    def _forbidden_phrases(self) -> list[str]:
        phrases = []
        for doc in self.documents:
            if doc.type != "interdits":
                continue
            section = re.split(r"^##\s+Formulations", doc.body, flags=re.MULTILINE)
            if len(section) < 2:
                continue
            for line in section[1].splitlines():
                if line.startswith("## "):
                    break
                m = re.match(r"\s*[-*]\s*[«\"“]?\s*(.+?)\s*[»\"”]?\s*$", line)
                if m:
                    phrases.append(m.group(1).lower())
        return phrases

    # ------------------------------------------------------------------ lecture
    def docs_for_pole(self, pole: str) -> list[Document]:
        return [d for d in self.documents if d.pole in (pole, "GROUPE")]

    def faq_for_pole(self, pole: str) -> list[FaqEntry]:
        return [f for f in self.faq if f.pole in (pole, "GROUPE")]

    def faq_by_id(self, faq_id: str) -> FaqEntry | None:
        return next((f for f in self.faq if f.id == faq_id), None)

    def search(self, query: str, pole: str | None = None, k: int = 4) -> list[Document]:
        q = set(_keywords(query))
        scored = []
        for d in self.documents:
            if pole and d.pole not in (pole, "GROUPE"):
                continue
            words = _keywords(d.titre + " " + d.body)
            score = sum(1 for w in words if w in q) / (1 + len(words) ** 0.5)
            if score > 0:
                scored.append((score, d))
        return [d for _, d in sorted(scored, key=lambda x: -x[0])[:k]]

    def search_passages(self, query: str, pole: str, k: int = 6,
                        types: tuple[str, ...] = ("guide", "faq", "catalogue", "fiche_pole",
                                                  "contacts")) -> list[Passage]:
        q = set(_keywords(query))
        scored = []
        for p in self.passages:
            if p.doc.type not in types or p.doc.pole not in (pole, "GROUPE"):
                continue
            words = _keywords(f"{p.doc.titre} {p.heading} {p.text}")
            score = sum(1 for w in words if w in q) / (1 + len(words) ** 0.5)
            if score > 0:
                scored.append((score, p))
        return [p for _, p in sorted(scored, key=lambda x: -x[0])[:k]]

    def passage(self, passage_id: str) -> Passage | None:
        return next((p for p in self.passages if p.id == passage_id), None)

    def context_for(self, pole: str, query: str = "", max_chars: int = 12000) -> str:
        """Contexte stable pour un pôle : charte, fiche, contacts, interdits, FAQ."""
        wanted = ("charte", "fiche_pole", "contacts", "interdits", "catalogue", "faq")
        docs = [d for d in self.docs_for_pole(pole) if d.type in wanted]
        if query:
            docs += [d for d in self.search(query, pole) if d not in docs]
        parts, size = [], 0
        for d in docs:
            chunk = f"### {d.titre} ({d.path})\n{d.body}\n"
            if size + len(chunk) > max_chars:
                break
            parts.append(chunk)
            size += len(chunk)
        return "\n".join(parts)

    # --------------------------------------------------------------- contrôle
    def verify_facts(self, text: str) -> list[FactIssue]:
        """Liste les faits du texte absents de la base (R-05 : zéro erreur publiée)."""
        issues: list[FactIssue] = []
        for e in _EMAIL.findall(text):
            if e.lower() not in self._emails:
                issues.append(FactIssue("email", e))
        text_wo_emails = _EMAIL.sub(" ", text)
        for u in _URL.findall(text_wo_emails):
            nu = _norm_url(u)
            if not any(nu == k or nu.startswith(k + "/") for k in self._urls):
                issues.append(FactIssue("url", u))
        text_wo_urls = _URL.sub(" ", text_wo_emails)
        for p in _PRICE.findall(text_wo_urls):
            if _digits(p) not in self._prices:
                issues.append(FactIssue("prix", p.strip()))
        text_wo_prices = _PRICE.sub(" ", text_wo_urls)
        for p in _PHONE.findall(text_wo_prices):
            if _DATE.fullmatch(p.strip()):
                continue
            d = _digits(p)
            if len(d) >= 8 and not any(d.endswith(k[-8:]) for k in self._phones if len(k) >= 8):
                issues.append(FactIssue("telephone", p.strip()))
        for p in _PERCENT.findall(text_wo_prices):
            if _digits(p) not in self._percents:
                issues.append(FactIssue("pourcentage", p.strip()))
        low = text.lower()
        for phrase in self._forbidden:
            if phrase and phrase in low:
                issues.append(FactIssue("formulation_interdite", phrase))
        return issues
