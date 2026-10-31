"""Coffre à secrets (section 13) : mots de passe des boîtes saisis dans le tableau de bord.

Chiffrés en base (Fernet : AES + HMAC) avec une clé dérivée de IBIG_SECRET_KEY ; jamais
affichés ni journalisés. Changer IBIG_SECRET_KEY oblige à ressaisir ces secrets.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    pass


def _fernet(secret_key: str) -> Fernet:
    if len(secret_key) < 32:
        raise VaultError("IBIG_SECRET_KEY trop courte pour chiffrer les secrets")
    digest = hashlib.sha256(b"ibig-vault:" + secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret_key: str, value: str) -> str:
    return _fernet(secret_key).encrypt(value.encode()).decode()


def decrypt(secret_key: str, token: str) -> str:
    try:
        return _fernet(secret_key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise VaultError("secret illisible : IBIG_SECRET_KEY a changé, ressaisir le "
                         "mot de passe de la boîte") from exc
