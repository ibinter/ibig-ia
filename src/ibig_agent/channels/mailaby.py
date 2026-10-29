"""Mailaby (module d'emailing de l'ERP IBIG) : envoi des campagnes validées (section 9).

Même interface que `BrevoClient` : le reste de l'agent ne change pas. Les « listes » sont
les étiquettes de contacts de Mailaby. Le point d'entrée `api_agent.php` (dossier
`integrations/mailaby/`) n'accepte que le jeton de l'agent et ne donne accès ni aux
contacts, ni aux mots de passe : seulement les étiquettes, les comptes d'envoi et la
création de campagne. Désinscription et suivi restent gérés par Mailaby.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from .emailing import ContactList, EmailingError

ENDPOINT = "api_agent.php"

# Quel compte d'envoi pour quel pôle : mot reconnu dans l'adresse. Modifiable par campagne.
POLE_KEYWORDS = {"SOFT": "ibigsoft", "EDUFORM": "eduform", "IMMOTRUST": "immotrust",
                 "MARKET": "ibig-market", "DIGITAL": "ibig-digital", "PARTNERS": "ibigpartners"}
PREFERRED_LOCAL = ("contact", "info", "formation")   # boîtes d'accueil
AVOIDED_LOCAL = ("recrutement", "support")            # pas pour des newsletters
PRINCIPAL = "contact@intermark-business.com"


def default_sender(pole_code: str, accounts: list[dict]) -> str:
    """Adresse d'envoi conseillée pour un pôle ; sans correspondance, le compte principal
    du groupe, sinon le premier compte. Chaîne vide s'il n'y a aucun compte."""
    emails = [str(a.get("from_email", "")).lower() for a in accounts if a.get("from_email")]
    keyword = POLE_KEYWORDS.get(pole_code, "")
    matches = [e for e in emails if keyword and keyword in e]

    def rank(email: str) -> tuple[int, int]:
        local = email.split("@", 1)[0]
        return (local in AVOIDED_LOCAL, local not in PREFERRED_LOCAL)

    if matches:
        return sorted(matches, key=rank)[0]
    return PRINCIPAL if PRINCIPAL in emails else (emails[0] if emails else "")


class MailabyClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        if not base_url or not token:
            raise EmailingError("Mailaby : adresse et jeton requis (menu « Services »)")
        self.url = f"{base_url.rstrip('/')}/{ENDPOINT}"
        self.client = client or httpx.Client(timeout=30)
        self.headers = {"X-Agent-Token": token, "accept": "application/json"}

    def _call(self, method: str, action: str, **kwargs) -> dict:
        resp = self.client.request(method, self.url, params={"action": action},
                                   headers=self.headers, **kwargs)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or not isinstance(data, dict):
            detail = data.get("error") if isinstance(data, dict) else ""
            raise EmailingError(f"Mailaby {resp.status_code} : "
                                f"{detail or resp.text[:150] or 'réponse illisible'}")
        return data

    def check(self) -> str:
        self._call("GET", "ping")
        accounts = self._call("GET", "accounts").get("accounts", [])
        return f"Mailaby OK ({len(accounts)} compte(s) d'envoi)"

    def accounts(self) -> list[dict]:
        """Tous les comptes d'envoi de Mailaby (id, label, from_name, from_email) : un seul
        raccordement donne accès aux 16, sans les saisir un par un."""
        return list(self._call("GET", "accounts").get("accounts", []))

    def lists(self) -> list[ContactList]:
        data = self._call("GET", "tags")
        out = [ContactList(0, "Tous les contacts actifs", int(data.get("total_actifs", 0)))]
        out += [ContactList(int(t["id"]), str(t["name"]), int(t.get("n", 0)))
                for t in data.get("tags", [])]
        return out

    def send_campaign(self, name: str, subject: str, html: str, sender_name: str,
                      sender_email: str, list_ids: list[int],
                      scheduled_at: datetime | None = None) -> dict:
        body = {"name": name[:190], "subject": subject[:500], "html": html,
                "from_email": sender_email, "from_name": sender_name,
                "tag_ids": [i for i in list_ids if i], "mode": "any", "launch": True}
        if scheduled_at is not None:
            body["send_at"] = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")
        created = self._call("POST", "campaign", json=body)
        if "id" not in created:
            raise EmailingError("Mailaby n'a pas renvoyé d'identifiant de campagne")
        return {"campaign_id": created["id"], "statut": created.get("status"),
                "destinataires": created.get("recipients"),
                "programmee": scheduled_at.isoformat() if scheduled_at else "envoi immédiat"}
