"""Publication automatique et programmée des posts validés (section 8).

1. Une publication validée (niveau 2) dont le compte est raccordé est **programmée** à sa
   date, à l'heure de publication réglée ; sinon elle est remise à l'équipe (manuel).
2. Toutes les 5 minutes, les publications arrivées à échéance sont publiées par l'action
   de niveau 1 « social.schedule_approved », qui ne publie **que** un texte déjà validé,
   relu en base : un agent ne peut rien y glisser d'autre.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .agents.communication import NETWORK_CHANNEL
from .auth import _sign
from .channels.social import FIELDS, PUBLISHERS, SocialError, publisher_for
from .config import Settings
from .configstore import service_value, set_service_value
from .db import PendingAction, ScheduledPost, ServiceSetting, utcnow
from .governance import ActionRequest, Executor, Governor, as_utc

CRED_PREFIX = "social:"
# Réseaux où l'on joint le visuel PNG (image publique signée) ; LinkedIn et X : texte seul
WITH_IMAGE = {"facebook_page", "instagram", "threads"}
MEDIA_SECONDS = 14 * 24 * 3600


def cred_name(reseau: str, compte: str) -> str:
    return f"{CRED_PREFIX}{reseau}:{compte}"


def load_credentials(sessions: sessionmaker[Session], settings: Settings, reseau: str,
                     compte: str) -> dict:
    raw = service_value(sessions, settings, cred_name(reseau, compte))
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def save_credentials(sessions: sessionmaker[Session], settings: Settings, reseau: str,
                     compte: str, values: dict, by: str) -> None:
    """Enregistre les accès (chiffrés) ; un champ laissé vide garde l'ancienne valeur."""
    current = load_credentials(sessions, settings, reseau, compte)
    merged = {k: (values.get(k) or "").strip() or current.get(k, "") for k, _ in FIELDS[reseau]}
    set_service_value(sessions, settings, cred_name(reseau, compte), json.dumps(merged), by,
                      secret=True)


def forget_credentials(sessions: sessionmaker[Session], reseau: str, compte: str) -> None:
    with sessions() as s:
        row = s.get(ServiceSetting, cred_name(reseau, compte))
        if row is not None:
            s.delete(row)
            s.commit()


def is_connected(sessions: sessionmaker[Session], settings: Settings, reseau: str,
                 compte: str) -> bool:
    creds = load_credentials(sessions, settings, reseau, compte)
    return reseau in PUBLISHERS and all(creds.get(k) for k, _ in FIELDS[reseau])


# ------------------------------------------------------------------ image publique signée
def media_token(secret: str, sid: int, now: float | None = None) -> str:
    """Lien public vers le visuel d'une publication programmée : Meta doit pouvoir le
    télécharger sans session. Propre à une publication, valable 14 jours."""
    expires = int((now or time.time()) + MEDIA_SECONDS)
    payload = f"{sid}.{expires}"
    return f"{payload}.{_sign(secret, 'media:' + payload)}"


def read_media_token(secret: str, token: str, now: float | None = None) -> int | None:
    import hmac

    try:
        sid, expires, signature = token.split(".")
        payload = f"{int(sid)}.{int(expires)}"
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(secret, "media:" + payload)):
        return None
    if int(expires) < (now or time.time()):
        return None
    return int(sid)


def publish_time(day_iso: str, settings: Settings, now: datetime | None = None) -> datetime:
    """Date prévue à l'heure de publication (fuseau IBIG) ; au plus tôt maintenant."""
    now = now or utcnow()
    try:
        day = date.fromisoformat(day_iso)
    except (TypeError, ValueError):
        return now
    local = datetime.combine(day, dtime(settings.social_publish_hour),
                             tzinfo=ZoneInfo(settings.timezone))
    return max(as_utc(local), as_utc(now))


def social_executors(sessions: sessionmaker[Session], settings: Settings,
                     client_factory=None) -> dict[str, Executor]:
    def post(payload: dict) -> dict:
        reseau, compte = payload.get("reseau", ""), payload.get("compte", "")
        if not is_connected(sessions, settings, reseau, compte):
            return {"mode": "manuel", "a_publier_par": "équipe communication",
                    "reseau": reseau, "compte": compte}
        when = publish_time(payload.get("date", ""), settings)
        pending_id = int(payload.get("_pending_id") or 0)
        with sessions() as s:
            pa = s.get(PendingAction, pending_id) if pending_id else None
            # Titre du visuel = sujet de la publication (fin du titre de l'action)
            titre = pa.title.split(" · ")[-1] if pa else payload.get("titre", "")
            sp = ScheduledPost(pending_id=pending_id, reseau=reseau, compte=compte,
                               pole=pa.pole if pa else payload.get("pole", ""),
                               texte=payload.get("texte", ""), titre=titre[:300],
                               publish_at=when)
            s.add(sp)
            s.commit()
            sid = sp.id
        return {"mode": "programme", "publication": sid, "le": when.isoformat(),
                "reseau": reseau, "compte": compte}

    def publish_approved(payload: dict) -> dict:
        with sessions() as s:
            sp = s.get(ScheduledPost, int(payload["publication"]))
            if sp is None or sp.status != "programme":
                raise SocialError("publication introuvable ou déjà traitée")
            sp.status = "en_cours"  # jamais deux fois, même si deux tâches se chevauchent
            s.commit()
            reseau, compte, texte, sid = sp.reseau, sp.compte, sp.texte, sp.id
        image = ""
        if reseau in WITH_IMAGE and settings.dashboard_url.startswith("https://"):
            image = (f"{settings.dashboard_url.rstrip('/')}/media/"
                     f"{media_token(settings.secret_key, sid)}.png")
        try:
            creds = load_credentials(sessions, settings, reseau, compte)
            client = client_factory() if client_factory else None
            result = publisher_for(reseau, creds, settings.whatsapp_api_version,
                                   client).publish(texte, image)
            status = "publie"
        except Exception as exc:  # noqa: BLE001 — l'échec est gardé et affiché
            result, status = {"erreur": str(exc)[:500]}, "echec"
        with sessions() as s:
            sp = s.get(ScheduledPost, sid)
            sp.status, sp.result = status, result
            s.commit()
        if status == "echec":
            raise SocialError(result["erreur"])
        return {"publie": True, "reseau": reseau, "compte": compte, **result}

    return {"social.post": post, "social.schedule_approved": publish_approved}


def publish_due(gov: Governor, sessions: sessionmaker[Session],
                now: datetime | None = None) -> int:
    """Publie les publications validées arrivées à échéance (tâche toutes les 5 min)."""
    now = now or utcnow()
    with sessions() as s:
        due = s.scalars(select(ScheduledPost).where(ScheduledPost.status == "programme")
                        .order_by(ScheduledPost.publish_at)).all()
        due = [(p.id, p.reseau, p.compte, p.pole, p.titre) for p in due
               if as_utc(p.publish_at) <= as_utc(now)]
    done = 0
    for sid, reseau, compte, pole, titre in due:
        channel = NETWORK_CHANNEL.get(reseau, reseau)
        if gov.is_stopped(channel):
            continue  # reprise automatique quand le canal est réactivé
        out = gov.submit(ActionRequest(
            agent="communication", action_type="social.schedule_approved", channel=channel,
            title=f"Publication programmée · {reseau} · {titre or compte}",
            payload={"publication": sid}, account=compte, pole=pole))
        done += out.status == "executed"
    return done
