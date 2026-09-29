import pytest

from ibig_agent.auth import hash_password, make_session, read_session, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("un-mot-de-passe-long")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("un-mot-de-passe-long", stored)
    assert not verify_password("autre-mot-de-passe", stored)
    assert not verify_password("x", "format-invalide")


def test_short_password_refused():
    with pytest.raises(ValueError):
        hash_password("court")


def test_session_signature_and_expiry():
    cookie = make_session("k" * 40, 7, now=1000)
    assert read_session("k" * 40, cookie, now=1001) == 7
    assert read_session("autre-cle" * 5, cookie, now=1001) is None
    assert read_session("k" * 40, cookie, now=1000 + 13 * 3600) is None
    assert read_session("k" * 40, cookie.replace("7:", "8:", 1), now=1001) is None
