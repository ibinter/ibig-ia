"""WhatsApp Business par la plateforme officielle de Meta (API Cloud), section 10.

* Réception : webhook signé par Meta (en-tête X-Hub-Signature-256, HMAC-SHA256 du corps
  avec le secret de l'application) ; toute requête non signée est refusée.
* Envoi : API Graph `/{phone_number_id}/messages`. Un message libre n'est permis que dans
  les 24 h qui suivent le dernier message du client ; au-delà, seul un modèle validé par
  Meta est permis (non géré ici : la réponse passe alors par un humain).

Jamais d'outil non officiel simulant WhatsApp Web : risque de bannissement du numéro.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..config import WhatsAppNumber

GRAPH = "https://graph.facebook.com"
WINDOW_HOURS = 24


def verify_signature(app_secret: str, body: bytes, header: str) -> bool:
    if not app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256=").strip().lower())


@dataclass
class InboundMessage:
    phone_number_id: str
    wa_id: str
    name: str
    message_id: str
    received_at: datetime
    type: str  # text | image | audio | document | location | …
    text: str


def parse_webhook(payload: dict) -> list[InboundMessage]:
    """Messages entrants d'une notification Meta (les accusés de lecture sont ignorés)."""
    out: list[InboundMessage] = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            pnid = str((value.get("metadata") or {}).get("phone_number_id", ""))
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                     for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                kind = m.get("type", "")
                if kind == "text":
                    text = (m.get("text") or {}).get("body", "")
                elif kind == "button":
                    text = (m.get("button") or {}).get("text", "")
                elif kind == "interactive":
                    inter = m.get("interactive") or {}
                    text = ((inter.get("button_reply") or inter.get("list_reply") or {})
                            .get("title", ""))
                else:
                    text = (m.get(kind) or {}).get("caption", "") if isinstance(
                        m.get(kind), dict) else ""
                try:
                    at = datetime.fromtimestamp(int(m.get("timestamp", 0)), UTC)
                except (TypeError, ValueError):
                    at = datetime.now(UTC)
                out.append(InboundMessage(pnid, str(m.get("from", "")),
                                          names.get(m.get("from"), ""), str(m.get("id", "")),
                                          at, kind, text[:4096]))
    return out


class WhatsAppClient:
    def __init__(self, number: WhatsAppNumber, api_version: str,
                 client: httpx.Client | None = None) -> None:
        self.number = number
        self.api_version = api_version
        self.client = client or httpx.Client(timeout=30)

    def _url(self, path: str = "") -> str:
        return f"{GRAPH}/{self.api_version}/{self.number.phone_number_id}{path}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.number.token()}"}

    def send_text(self, to: str, body: str) -> dict:
        resp = self.client.post(self._url("/messages"), headers=self._headers(), json={
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
            "type": "text", "text": {"preview_url": False, "body": body[:4096]},
        })
        if resp.status_code != 200:
            raise RuntimeError(f"API WhatsApp {resp.status_code} : {resp.text[:300]}")
        ids = [m.get("id") for m in resp.json().get("messages", [])]
        return {"whatsapp_id": ids[0] if ids else None}

    def check(self) -> str:
        """Jeton valide et état du numéro, sans rien envoyer (diagnostic)."""
        resp = self.client.get(self._url(), headers=self._headers(), params={
            "fields": "display_phone_number,verified_name,quality_rating"})
        if resp.status_code != 200:
            raise RuntimeError(f"API WhatsApp {resp.status_code} : {resp.text[:200]}")
        data = resp.json()
        quality = data.get("quality_rating", "UNKNOWN")
        if quality == "RED":
            raise RuntimeError("qualité du numéro RED : envois limités par Meta, à traiter")
        note = "" if quality in ("GREEN", "UNKNOWN") else " — à surveiller"
        return (f"{data.get('verified_name', '?')} ({data.get('display_phone_number', '?')}), "
                f"qualité {quality}{note}")
