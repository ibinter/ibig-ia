"""Publication automatique sur les réseaux sociaux (section 8), par les API officielles.

Chaque compte se raccorde dans le tableau de bord (menu Services) avec ses propres accès,
chiffrés dans le coffre. Une publication n'est publiée qu'après validation (niveau 2),
à sa date, par la programmation (niveau 1 : « programmation de posts déjà validés »).

Groupes Facebook, TikTok et chaînes WhatsApp n'ont pas d'API de publication ouverte :
l'agent les prépare, un humain publie (section 8).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

GRAPH = "https://graph.facebook.com"
THREADS = "https://graph.threads.net/v1.0"
LINKEDIN = "https://api.linkedin.com/rest"
X_TWEETS = "https://api.x.com/2/tweets"

# Champs d'accès attendus par réseau (saisis au tableau de bord)
FIELDS: dict[str, list[tuple[str, str]]] = {
    "facebook_page": [("page_id", "Identifiant de la page"),
                      ("token", "Jeton d'accès de la page")],
    "instagram": [("ig_user_id", "Identifiant du compte Instagram professionnel"),
                  ("token", "Jeton d'accès (page Facebook reliée)")],
    "threads": [("user_id", "Identifiant Threads"), ("token", "Jeton d'accès Threads")],
    "linkedin": [("org_id", "Identifiant de la page entreprise (chiffres)"),
                 ("token", "Jeton d'accès LinkedIn (w_organization_social)")],
    "x": [("consumer_key", "API Key"), ("consumer_secret", "API Key Secret"),
          ("access_token", "Access Token"), ("access_secret", "Access Token Secret")],
}
NEEDS_IMAGE = {"instagram"}


class SocialError(RuntimeError):
    pass


def _check(resp: httpx.Response, name: str) -> dict:
    if resp.status_code >= 400:
        raise SocialError(f"{name} {resp.status_code} : {resp.text[:250]}")
    return resp.json() if resp.content else {}


class FacebookPage:
    def __init__(self, creds: dict, version: str, client: httpx.Client) -> None:
        self.creds, self.version, self.client = creds, version, client

    def publish(self, text: str, image_url: str = "") -> dict:
        base = f"{GRAPH}/{self.version}/{self.creds['page_id']}"
        if image_url:
            data = _check(self.client.post(f"{base}/photos", data={
                "url": image_url, "caption": text,
                "access_token": self.creds["token"]}), "Facebook")
        else:
            data = _check(self.client.post(f"{base}/feed", data={
                "message": text, "access_token": self.creds["token"]}), "Facebook")
        return {"id": data.get("post_id") or data.get("id")}

    def check(self) -> str:
        data = _check(self.client.get(f"{GRAPH}/{self.version}/{self.creds['page_id']}",
                                      params={"fields": "name",
                                              "access_token": self.creds["token"]}),
                      "Facebook")
        return f"Page « {data.get('name', '?')} » accessible"

    def comments(self, limit: int = 25) -> list[dict]:
        """Commentaires récents des publications de la page (veille e-réputation)."""
        data = _check(self.client.get(
            f"{GRAPH}/{self.version}/{self.creds['page_id']}/feed",
            params={"fields": "id,comments.limit(25){id,message,created_time,from}",
                    "limit": limit, "access_token": self.creds["token"]}), "Facebook")
        out = []
        for post in data.get("data", []):
            for c in (post.get("comments") or {}).get("data", []):
                out.append({"id": c.get("id"), "post_id": post.get("id"),
                            "text": c.get("message", ""), "at": c.get("created_time", ""),
                            "author": (c.get("from") or {}).get("name", "")})
        return out


class Instagram:
    def __init__(self, creds: dict, version: str, client: httpx.Client) -> None:
        self.creds, self.version, self.client = creds, version, client

    def publish(self, text: str, image_url: str = "") -> dict:
        if not image_url:
            raise SocialError("Instagram exige une image")
        base = f"{GRAPH}/{self.version}/{self.creds['ig_user_id']}"
        media = _check(self.client.post(f"{base}/media", data={
            "image_url": image_url, "caption": text, "access_token": self.creds["token"]}),
            "Instagram")
        done = _check(self.client.post(f"{base}/media_publish", data={
            "creation_id": media["id"], "access_token": self.creds["token"]}), "Instagram")
        return {"id": done.get("id")}

    def check(self) -> str:
        data = _check(self.client.get(f"{GRAPH}/{self.version}/{self.creds['ig_user_id']}",
                                      params={"fields": "username",
                                              "access_token": self.creds["token"]}),
                      "Instagram")
        return f"Compte @{data.get('username', '?')} accessible"


class Threads:
    def __init__(self, creds: dict, version: str, client: httpx.Client) -> None:
        self.creds, self.client = creds, client

    def publish(self, text: str, image_url: str = "") -> dict:
        base = f"{THREADS}/{self.creds['user_id']}"
        params = {"text": text[:500], "access_token": self.creds["token"],
                  "media_type": "IMAGE" if image_url else "TEXT"}
        if image_url:
            params["image_url"] = image_url
        media = _check(self.client.post(f"{base}/threads", data=params), "Threads")
        done = _check(self.client.post(f"{base}/threads_publish", data={
            "creation_id": media["id"], "access_token": self.creds["token"]}), "Threads")
        return {"id": done.get("id")}

    def check(self) -> str:
        data = _check(self.client.get(f"{THREADS}/me", params={
            "fields": "username", "access_token": self.creds["token"]}), "Threads")
        return f"Compte Threads @{data.get('username', '?')} accessible"


class LinkedInPage:
    VERSION = "202409"

    def __init__(self, creds: dict, version: str, client: httpx.Client) -> None:
        self.creds, self.client = creds, client

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.creds['token']}",
                "LinkedIn-Version": self.VERSION, "X-Restli-Protocol-Version": "2.0.0"}

    def publish(self, text: str, image_url: str = "") -> dict:
        body = {"author": f"urn:li:organization:{self.creds['org_id']}",
                "commentary": text[:3000], "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                 "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        resp = self.client.post(f"{LINKEDIN}/posts", json=body, headers=self._headers())
        _check(resp, "LinkedIn")
        return {"id": resp.headers.get("x-restli-id", "")}

    def check(self) -> str:
        _check(self.client.get(f"{LINKEDIN}/organizations/{self.creds['org_id']}",
                               headers=self._headers()), "LinkedIn")
        return "Page LinkedIn accessible"


def _pct(s: str) -> str:
    return quote(s, safe="-._~")


def oauth1_header(method: str, url: str, creds: dict, nonce: str = "",
                  timestamp: str = "") -> str:
    """En-tête OAuth 1.0a (HMAC-SHA1) pour l'API X ; le corps JSON n'est pas signé."""
    oauth = {"oauth_consumer_key": creds["consumer_key"],
             "oauth_nonce": nonce or secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1",
             "oauth_timestamp": timestamp or str(int(time.time())),
             "oauth_token": creds["access_token"], "oauth_version": "1.0"}
    params = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(oauth.items()))
    base = "&".join([method.upper(), _pct(url), _pct(params)])
    key = f"{_pct(creds['consumer_secret'])}&{_pct(creds['access_secret'])}"
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


class XAccount:
    def __init__(self, creds: dict, version: str, client: httpx.Client) -> None:
        self.creds, self.client = creds, client

    def publish(self, text: str, image_url: str = "") -> dict:
        resp = self.client.post(X_TWEETS, json={"text": text[:280]}, headers={
            "Authorization": oauth1_header("POST", X_TWEETS, self.creds)})
        data = _check(resp, "X")
        return {"id": (data.get("data") or {}).get("id")}

    def check(self) -> str:
        url = "https://api.x.com/2/users/me"
        data = _check(self.client.get(url, headers={
            "Authorization": oauth1_header("GET", url, self.creds)}), "X")
        return f"Compte X @{(data.get('data') or {}).get('username', '?')} accessible"


PUBLISHERS = {"facebook_page": FacebookPage, "instagram": Instagram, "threads": Threads,
              "linkedin": LinkedInPage, "x": XAccount}

# ------------------------------------------------------------------ outils multi-comptes
# Un compte peut passer par un relais (Metricool, Buffer) au lieu des API de chaque réseau :
# un seul accès pour plusieurs comptes. La gouvernance reste identique : le relais ne
# reçoit que le texte déjà validé, publié à l'échéance par « social.schedule_approved ».
RELAY_FIELDS: list[tuple[str, str]] = [
    ("relais", "Relais : metricool ou buffer (vide = connexion directe au réseau)"),
    ("relais_jeton", "Relais : jeton d'accès (Metricool X-Mc-Auth, ou Buffer access token)"),
    ("relais_profil", "Relais : Metricool « blogId:userId » · Buffer identifiant du profil"),
]
METRICOOL_NETWORK = {"facebook_page": "facebook", "instagram": "instagram",
                     "threads": "threads", "linkedin": "linkedin", "x": "twitter"}
METRICOOL = "https://app.metricool.com/api"
BUFFER = "https://api.bufferapp.com/1"


def all_fields(reseau: str) -> list[tuple[str, str]]:
    """Champs saisissables d'un compte : accès directs, puis relais."""
    return (FIELDS.get(reseau, []) + RELAY_FIELDS) if reseau in PUBLISHERS else []


def relay_complete(creds: dict) -> bool:
    return creds.get("relais") in RELAYS and all(creds.get(k) for k in
                                                 ("relais_jeton", "relais_profil"))


def direct_complete(reseau: str, creds: dict) -> bool:
    return reseau in PUBLISHERS and all(creds.get(k) for k, _ in FIELDS[reseau])


class MetricoolRelay:
    def __init__(self, reseau: str, creds: dict, version: str, client: httpx.Client) -> None:
        self.reseau, self.creds, self.client = reseau, creds, client
        try:
            self.blog, self.user = creds["relais_profil"].split(":", 1)
        except ValueError as exc:
            raise SocialError("Metricool : profil attendu sous la forme blogId:userId") from exc

    def _headers(self) -> dict:
        return {"X-Mc-Auth": self.creds["relais_jeton"]}

    def publish(self, text: str, image_url: str = "") -> dict:
        # Metricool refuse une date passée : on programme dans la minute qui suit
        at = (datetime.now(UTC) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
        body = {"text": text, "autoPublish": True,
                "providers": [{"network": METRICOOL_NETWORK[self.reseau]}],
                "publicationDate": {"dateTime": at, "timezone": "UTC"},
                "media": [image_url] if image_url else []}
        data = _check(self.client.post(f"{METRICOOL}/v2/scheduler/posts", json=body,
                                       params={"blogId": self.blog, "userId": self.user},
                                       headers=self._headers()), "Metricool")
        return {"id": str((data.get("data") or data).get("id", "")), "relais": "metricool"}

    def check(self) -> str:
        data = _check(self.client.get(f"{METRICOOL}/admin/simpleProfiles",
                                      params={"userId": self.user},
                                      headers=self._headers()), "Metricool")
        brands = data if isinstance(data, list) else data.get("data", [])
        if not any(str(b.get("id")) == self.blog for b in brands):
            raise SocialError(f"Metricool : marque {self.blog} introuvable pour ce jeton")
        return "Marque Metricool accessible"


class BufferRelay:
    def __init__(self, reseau: str, creds: dict, version: str, client: httpx.Client) -> None:
        self.reseau, self.creds, self.client = reseau, creds, client

    def publish(self, text: str, image_url: str = "") -> dict:
        form = {"access_token": self.creds["relais_jeton"], "text": text, "now": "true",
                "profile_ids[]": self.creds["relais_profil"]}
        if image_url:
            form["media[photo]"] = image_url
        data = _check(self.client.post(f"{BUFFER}/updates/create.json", data=form), "Buffer")
        if data.get("success") is False:
            raise SocialError(f"Buffer : {data.get('message', 'refusé')}")
        updates = data.get("updates") or [{}]
        return {"id": str(updates[0].get("id", "")), "relais": "buffer"}

    def check(self) -> str:
        data = _check(self.client.get(
            f"{BUFFER}/profiles/{self.creds['relais_profil']}.json",
            params={"access_token": self.creds["relais_jeton"]}), "Buffer")
        return f"Profil Buffer « {data.get('formatted_username', '?')} » accessible"


RELAYS = {"metricool": MetricoolRelay, "buffer": BufferRelay}


def publisher_for(reseau: str, creds: dict, version: str = "v21.0",
                  client: httpx.Client | None = None):
    cls = PUBLISHERS.get(reseau)
    if cls is None:
        raise SocialError(f"Pas de publication automatique pour {reseau}")
    if creds.get("relais"):
        if not relay_complete(creds):
            raise SocialError("Relais incomplet : choisissez metricool ou buffer, avec "
                              "jeton et profil")
        return RELAYS[creds["relais"]](reseau, creds, version, client or httpx.Client(timeout=30))
    missing = [k for k, _ in FIELDS[reseau] if not creds.get(k)]
    if missing:
        raise SocialError(f"Accès incomplets pour {reseau} : {', '.join(missing)}")
    return cls(creds, version, client or httpx.Client(timeout=30))
