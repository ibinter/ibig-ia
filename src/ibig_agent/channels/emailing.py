"""Service d'emailing (section 9) : newsletters et campagnes ne partent JAMAIS des boîtes
LWS ou Gmail, mais d'un service dédié avec un domaine authentifié (SPF, DKIM, DMARC).

Brevo (API v3). Les listes de contacts, le consentement et la désinscription sont gérés
dans Brevo : l'agent ne fait que rédiger la campagne, validée par un humain (niveau 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

BREVO_API = "https://api.brevo.com/v3"


class EmailingError(RuntimeError):
    pass


@dataclass
class ContactList:
    id: int
    name: str
    subscribers: int


class BrevoClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise EmailingError("Clé API Brevo absente : menu « Services »")
        self.client = client or httpx.Client(timeout=30)
        self.headers = {"api-key": api_key, "accept": "application/json",
                        "content-type": "application/json"}

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = self.client.request(method, BREVO_API + path, headers=self.headers, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", resp.text)
            except ValueError:
                detail = resp.text
            raise EmailingError(f"Brevo {resp.status_code} : {detail[:200]}")
        return resp.json() if resp.content else {}

    def check(self) -> str:
        account = self._call("GET", "/account")
        return f"Brevo OK ({account.get('companyName') or account.get('email', 'compte')})"

    def lists(self) -> list[ContactList]:
        data = self._call("GET", "/contacts/lists", params={"limit": 50, "offset": 0})
        return [ContactList(int(x["id"]), x.get("name", ""),
                            int(x.get("uniqueSubscribers", x.get("totalSubscribers", 0)) or 0))
                for x in data.get("lists", [])]

    def send_campaign(self, name: str, subject: str, html: str, sender_name: str,
                      sender_email: str, list_ids: list[int],
                      scheduled_at: datetime | None = None) -> dict:
        body = {"name": name[:250], "subject": subject[:250],
                "sender": {"name": sender_name, "email": sender_email},
                "htmlContent": html, "recipients": {"listIds": list_ids}}
        if scheduled_at is not None:
            body["scheduledAt"] = scheduled_at.isoformat()
        created = self._call("POST", "/emailCampaigns", json=body)
        cid = created.get("id")
        if cid is None:
            raise EmailingError("Brevo n'a pas renvoyé d'identifiant de campagne")
        if scheduled_at is None:
            self._call("POST", f"/emailCampaigns/{cid}/sendNow")
        return {"campaign_id": cid, "programmee": scheduled_at.isoformat() if scheduled_at
                else "envoi immédiat"}
