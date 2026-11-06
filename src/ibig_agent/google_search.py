"""Positions sur Google : lecture, chaque nuit, des statistiques de Google Search Console (lecture seule).

Le compte de service « Agent IA IBIG lecture Search Console » est ajouté en « accès limité » dans chaque
propriété. Sa clé est dans le coffre chiffré de l'agent. Aucune dépendance Google : le jeton est obtenu avec
une signature RSA (bibliothèque cryptography) puis les données sont lues avec httpx.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .configstore import service_value, set_service_value
from .db import utcnow

KEY_NAME = "google_search_console_key"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
DOMAINS = ["ibig-market.com", "ibigsoft.com", "ibig-eduform.com", "ibigimmotrust.com", "patricekouakou.com",
           "ibig-digital.com", "intermark-business.com"]
DAYS = 28
LAG = 2                      # Google publie ses chiffres avec ~2 jours de retard


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _round(value: float, digits: int = 1) -> float:
    return round(float(value), digits)


def _today() -> date:
    return datetime.now(UTC).date()


class GoogleSearchService:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings, client: httpx.Client | None = None,
                 domains: list[str] | None = None, today: Callable[[], date] = _today):
        self._sessions = sessions
        self._settings = settings
        self._client = client
        self._domains = domains or DOMAINS
        self._today = today

    # ---------------------------------------------------------------- accès
    def configured(self) -> bool:
        return bool(service_value(self._sessions, self._settings, KEY_NAME))

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=40)

    def _token(self, http: httpx.Client) -> str:
        info = json.loads(service_value(self._sessions, self._settings, KEY_NAME) or "{}")
        if not info.get("private_key"):
            raise ValueError("La clé Google n'est pas configurée")
        now = int(time.time())
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        body = _b64(json.dumps({"iss": info["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                                "iat": now, "exp": now + 3000}).encode())
        key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        resp = http.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": f"{head}.{body}.{_b64(sig)}"})
        if resp.status_code != 200:
            raise ValueError(f"Google a refusé la connexion ({resp.status_code})")
        return resp.json()["access_token"]

    def _query(self, http: httpx.Client, token: str, domain: str, start: date, end: date,
               dimensions: list[str] | None = None, limit: int = 10) -> list[dict]:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": limit}
        if dimensions:
            body["dimensions"] = dimensions
        resp = http.post(API.format(site=quote(f"sc-domain:{domain}", safe="")), json=body,
                         headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 403:
            raise ValueError("Accès refusé : le compte de service n'est pas ajouté à cette propriété")
        if resp.status_code != 200:
            raise ValueError(f"Google a répondu {resp.status_code}")
        return resp.json().get("rows", [])

    # ---------------------------------------------------------------- synchronisation
    def sync_domain(self, http: httpx.Client, token: str, domain: str) -> dict:
        end = self._today() - timedelta(days=LAG)
        start = end - timedelta(days=DAYS - 1)
        prev_end, prev_start = start - timedelta(days=1), start - timedelta(days=DAYS)
        total = (self._query(http, token, domain, start, end) or [{}])[0]
        before = (self._query(http, token, domain, prev_start, prev_end) or [{}])[0]
        daily = self._query(http, token, domain, start, end, ["date"], DAYS)
        queries = self._query(http, token, domain, start, end, ["query"], 10)
        pages = self._query(http, token, domain, start, end, ["page"], 10)
        result = {
            "at": utcnow().isoformat(timespec="seconds"), "from": start.isoformat(), "to": end.isoformat(),
            "clicks": int(total.get("clicks", 0)), "impressions": int(total.get("impressions", 0)),
            "ctr": _round(total.get("ctr", 0) * 100, 2), "position": _round(total.get("position", 0)),
            "prev_clicks": int(before.get("clicks", 0)), "prev_impressions": int(before.get("impressions", 0)),
            "prev_position": _round(before.get("position", 0)),
            "daily": [{"day": r["keys"][0], "clicks": int(r["clicks"]), "impressions": int(r["impressions"])}
                      for r in daily],
            "queries": [{"q": r["keys"][0], "clicks": int(r["clicks"]), "impressions": int(r["impressions"]),
                         "position": _round(r["position"])} for r in queries],
            "pages": [{"p": r["keys"][0], "clicks": int(r["clicks"]), "impressions": int(r["impressions"]),
                       "position": _round(r["position"])} for r in pages],
        }
        set_service_value(self._sessions, self._settings, f"gsc_{domain}", json.dumps(result), "synchronisation")
        return result

    def sync(self) -> dict:
        """Lit tous les sites ; un site en erreur n'arrête pas les autres."""
        http = self._http()
        token = self._token(http)
        done, errors = [], {}
        for domain in self._domains:
            try:
                self.sync_domain(http, token, domain)
                done.append(domain)
            except (ValueError, httpx.HTTPError) as exc:
                errors[domain] = str(exc)[:200]
        set_service_value(self._sessions, self._settings, "gsc_errors", json.dumps(errors), "synchronisation")
        return {"sites": len(done), "erreurs": errors}

    # ---------------------------------------------------------------- lecture
    def overview(self) -> list[dict]:
        out = []
        errors = json.loads(service_value(self._sessions, self._settings, "gsc_errors") or "{}")
        for domain in self._domains:
            raw = service_value(self._sessions, self._settings, f"gsc_{domain}")
            data = json.loads(raw) if raw else None
            out.append({"domain": domain, "data": data, "error": errors.get(domain, "")})
        return sorted(out, key=lambda r: -(r["data"]["clicks"] if r["data"] else -1))

    def detail(self, domain: str) -> dict | None:
        raw = service_value(self._sessions, self._settings, f"gsc_{domain}") if domain in self._domains else ""
        return json.loads(raw) if raw else None
