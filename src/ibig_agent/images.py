"""Génération de photos pour les publications (section 14 : « génération d'images »).

Claude ne produit pas d'images : la photo est générée par l'API Images d'OpenAI
(gpt-image-1) à partir du brief visuel écrit par l'agent Communication, à la demande d'un
humain (bouton « Générer une photo » des Validations : chaque image a un coût). Elle sert
de fond au visuel aux couleurs IBIG ; le texte et la marque sont ajoutés par l'agent,
jamais par le générateur (qui écrit mal et invente des logos).
"""

from __future__ import annotations

import base64
import io

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import AIUsage, MediaAsset, PendingAction, utcnow

OPENAI_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_MODEL = "gpt-image-1"
# Coût indicatif par image, qualité « medium » (USD, à revérifier sur openai.com/pricing)
PRICES = {"1024x1024": 0.042, "1024x1536": 0.063, "1536x1024": 0.063}


class ImageError(RuntimeError):
    pass


class OpenAIImages:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 client: httpx.Client | None = None) -> None:
        self.api_key, self.model = api_key, model or DEFAULT_MODEL
        self.client = client or httpx.Client(timeout=180)

    def generate(self, prompt: str, size: str) -> bytes:
        resp = self.client.post(OPENAI_URL, headers={"Authorization": f"Bearer {self.api_key}"},
                                json={"model": self.model, "prompt": prompt[:4000], "n": 1,
                                      "size": size, "quality": "medium"})
        if resp.status_code != 200:
            try:
                detail = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = resp.text[:200]
            raise ImageError(f"OpenAI {resp.status_code} : {detail[:200]}")
        data = (resp.json().get("data") or [{}])[0]
        if data.get("b64_json"):
            return base64.b64decode(data["b64_json"])
        if data.get("url"):  # anciens modèles : lien temporaire
            return self.client.get(data["url"]).content
        raise ImageError("OpenAI : aucune image reçue")

    def check(self) -> str:
        resp = self.client.get("https://api.openai.com/v1/models/" + self.model,
                               headers={"Authorization": f"Bearer {self.api_key}"})
        if resp.status_code != 200:
            raise ImageError(f"OpenAI {resp.status_code} : {resp.text[:200]}")
        return f"Clé valide, modèle {self.model} disponible"


def size_for(reseau: str) -> str:
    from .dashboard.visuals import FORMATS

    w, h = FORMATS.get(reseau, (1080, 1080))
    if h / w > 1.15:
        return "1024x1536"
    if w / h > 1.15:
        return "1536x1024"
    return "1024x1024"


def photo_prompt(brief: str, sujet: str, activite: str) -> str:
    return (
        "Photographie réaliste et professionnelle, lumière naturelle, couleurs chaleureuses, "
        "cadrage soigné, style publicité corporate haut de gamme. Contexte : Côte d'Ivoire, "
        "Afrique de l'Ouest ; personnes africaines, tenues professionnelles et modernes, "
        "expressions naturelles et positives. "
        f"Secteur : {activite or 'services aux entreprises'}. Sujet : {sujet}. "
        f"Brief : {brief or sujet}. "
        "Laisser de l'espace libre en haut et à gauche pour un titre. "
        "IMPORTANT : aucune écriture, aucun texte, aucune lettre, aucun chiffre, aucun logo, "
        "aucune marque, aucun filigrane dans l'image.")


def month_spend(sessions: sessionmaker[Session]) -> float:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with sessions() as s:
        return float(s.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0))
                              .where(AIUsage.created_at >= start)) or 0.0)


def to_jpeg(raw: bytes) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def photo_for(sessions: sessionmaker[Session], pending_id: int) -> bytes | None:
    with sessions() as s:
        row = s.scalars(select(MediaAsset).where(MediaAsset.pending_id == pending_id)
                        .order_by(MediaAsset.id.desc())).first()
    return row.data if row is not None else None


def generate_photo(sessions: sessionmaker[Session], settings: Settings, pa: PendingAction,
                   activite: str, by: str, client_factory=None) -> int:
    """Génère la photo d'une publication en attente et la range ; renvoie son numéro."""
    from .configstore import service_value

    key = service_value(sessions, settings, "openai_api_key")
    if not key:
        raise ImageError("Aucune clé OpenAI : menu Services")
    size = size_for(pa.payload.get("reseau", ""))
    cost = PRICES.get(size, 0.063)
    if month_spend(sessions) + cost > settings.monthly_ai_budget_usd:
        raise ImageError("Plafond mensuel de dépense IA atteint")
    model = service_value(sessions, settings, "openai_image_model") or DEFAULT_MODEL
    prompt = photo_prompt(pa.payload.get("brief_visuel", ""), pa.title.split(" · ")[-1],
                          activite)
    raw = (client_factory or OpenAIImages)(key, model).generate(prompt, size)
    data = to_jpeg(raw)
    with sessions() as s:
        for old in s.scalars(select(MediaAsset).where(MediaAsset.pending_id == pa.id)).all():
            s.delete(old)
        asset = MediaAsset(pending_id=pa.id, mime="image/jpeg", data=data, prompt=prompt,
                           created_by=by)
        s.add(asset)
        s.add(AIUsage(model=model, purpose="image.photo", input_tokens=0, output_tokens=0,
                      cost_usd=cost))
        s.commit()
        return asset.id
