"""Comptes nominatifs et droits du tableau de bord (section 12 : un valideur par pôle).

* admin     : tout, y compris la gestion des comptes ;
* direction : valide tous les pôles et traite les dossiers de niveau 3 ;
* valideur  : valide le niveau 2 des pôles dont il est valideur ou suppléant
              (config/poles.yaml, par adresse mail).

Tout le monde peut suspendre un canal ; seuls admin et direction le réactivent.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import OrgConfig
from .db import PendingAction, User

ROLES = ("admin", "direction", "valideur")
_ITERATIONS = 310_000
SESSION_SECONDS = 12 * 3600
ACTION_LINK_SECONDS = 48 * 3600


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Le mot de passe doit faire au moins 10 caractères")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                     int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


# ------------------------------------------------------------------ sessions
def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(secret: str, user_id: int, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    payload = f"{user_id}:{expires}"
    return f"{payload}:{_sign(secret, payload)}"


def read_session(secret: str, cookie: str, now: float | None = None) -> int | None:
    try:
        user_id, expires, signature = cookie.split(":")
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(secret, f"{user_id}:{expires}")):
        return None
    if int(expires) < (now or time.time()):
        return None
    return int(user_id)


# ------------------------------------------------------------------ liens de validation
def make_action_token(secret: str, pending_id: int, user_id: int,
                      now: float | None = None) -> str:
    """Lien « valider en un clic » envoyé par mail (section 12) : propre à une action et
    à une personne, valable 48 h. Il ouvre une page de confirmation, sans rien exécuter."""
    expires = int((now or time.time()) + ACTION_LINK_SECONDS)
    payload = f"{pending_id}.{user_id}.{expires}"
    return f"{payload}.{_sign(secret, 'action:' + payload)}"


def read_action_token(secret: str, token: str,
                      now: float | None = None) -> tuple[int, int] | None:
    try:
        pending_id, user_id, expires, signature = token.split(".")
        payload = f"{int(pending_id)}.{int(user_id)}.{int(expires)}"
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(secret, "action:" + payload)):
        return None
    if int(expires) < (now or time.time()):
        return None
    return int(pending_id), int(user_id)


def normalize_phone(phone: str) -> str:
    """Numéro international en chiffres seuls (format des identifiants WhatsApp)."""
    digits = re.sub(r"\D", "", phone or "").removeprefix("00")
    return digits if 8 <= len(digits) <= 15 else ""


# ------------------------------------------------------------------ droits
@dataclass(frozen=True)
class Principal:
    id: int
    email: str
    name: str
    role: str
    poles: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.name} <{self.email}>"

    def can_decide(self, pa: PendingAction) -> bool:
        """Valider ou rejeter une action de niveau 2."""
        if self.role in ("admin", "direction"):
            return True
        return pa.pole in self.poles

    def can_handle_level3(self) -> bool:
        return self.role in ("admin", "direction")

    def can_resume(self) -> bool:
        return self.role in ("admin", "direction")


class UserStore:
    def __init__(self, session_factory: sessionmaker[Session], org: OrgConfig) -> None:
        self._sessions = session_factory
        self.org = org

    def create(self, email: str, name: str, role: str, password: str) -> User:
        if role not in ROLES:
            raise ValueError(f"Rôle inconnu : {role} (attendu : {', '.join(ROLES)})")
        with self._sessions() as s:
            if s.scalar(select(User).where(User.email == email.lower())):
                raise ValueError(f"Un compte existe déjà pour {email}")
            user = User(email=email.lower(), name=name, role=role,
                        password_hash=hash_password(password))
            s.add(user)
            s.commit()
            return user

    def set_password(self, email: str, password: str) -> None:
        with self._sessions() as s:
            user = s.scalar(select(User).where(User.email == email.lower()))
            if user is None:
                raise ValueError(f"Aucun compte pour {email}")
            user.password_hash = hash_password(password)
            s.commit()

    def set_active(self, email: str, active: bool) -> None:
        with self._sessions() as s:
            user = s.scalar(select(User).where(User.email == email.lower()))
            if user is None:
                raise ValueError(f"Aucun compte pour {email}")
            user.active = active
            s.commit()

    def authenticate(self, email: str, password: str) -> Principal | None:
        with self._sessions() as s:
            user = s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not user.active:
            # Même coût qu'un vrai contrôle, pour ne pas révéler les comptes existants.
            verify_password(password, "pbkdf2_sha256$310000$00$00")
            return None
        if not verify_password(password, user.password_hash):
            return None
        return self._principal(user)

    def get(self, user_id: int) -> Principal | None:
        with self._sessions() as s:
            user = s.get(User, user_id)
        if user is None or not user.active:
            return None
        return self._principal(user)

    def by_email(self, email: str) -> Principal | None:
        with self._sessions() as s:
            user = s.scalars(select(User).where(User.email == email.strip().lower())).first()
        if user is None or not user.active:
            return None
        return self._principal(user)

    def by_phone(self, phone: str) -> Principal | None:
        digits = normalize_phone(phone)
        if not digits:
            return None
        with self._sessions() as s:
            user = s.scalars(select(User).where(User.phone == digits,
                                                User.active.is_(True))).first()
        return self._principal(user) if user is not None else None

    def set_phone(self, user_id: int, phone: str) -> str:
        digits = normalize_phone(phone)
        if phone.strip() and not digits:
            raise ValueError("Numéro invalide : format international, ex. 225 07 00 00 00 00")
        with self._sessions() as s:
            user = s.get(User, user_id)
            if user is None:
                raise ValueError("Compte introuvable")
            user.phone = digits
            s.commit()
        return digits

    def all(self) -> list[User]:
        with self._sessions() as s:
            return list(s.scalars(select(User).order_by(User.name)).all())

    def _principal(self, user: User) -> Principal:
        return Principal(user.id, user.email, user.name, user.role,
                         tuple(self.org.poles_of(user.email)))
