"""Provider self-service token signing. Regression: tokens must be signed with
the app's configured secret (settings.secret_key), NOT a hardcoded fallback —
a guessable secret would let anyone forge a provider link to patient PHI."""
import jwt
import pytest

from app.config import settings
from app.services import missing_charges_token as tok


def test_mint_decode_roundtrip():
    t = tok.mint_token("Salley, Danielle")
    payload = tok.decode_token(t)
    assert payload and payload["provider"] == "Salley, Danielle"
    assert payload["kind"] == tok.KIND and payload["iss"] == tok.ISSUER


def test_secret_is_app_secret_not_hardcoded(monkeypatch):
    monkeypatch.delenv("MISSING_CHARGES_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(settings, "secret_key", "a-real-configured-secret")
    assert tok._secret() == "a-real-configured-secret"
    # The old hardcoded fallback must no longer sign anything.
    assert tok._secret() != "dev-only-do-not-use-in-production"


def test_env_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("MISSING_CHARGES_TOKEN_SECRET", "dedicated-secret")
    monkeypatch.setattr(settings, "secret_key", "app-secret")
    assert tok._secret() == "dedicated-secret"


def test_token_forged_with_wrong_secret_is_rejected(monkeypatch):
    monkeypatch.delenv("MISSING_CHARGES_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(settings, "secret_key", "the-true-secret")
    # An attacker signing with the old well-known string must not validate.
    forged = jwt.encode(
        {"provider": "Salley, Danielle", "iss": tok.ISSUER, "kind": tok.KIND,
         "iat": 0, "exp": 9999999999},
        "dev-only-do-not-use-in-production", algorithm=tok.ALGORITHM)
    assert tok.decode_token(forged) is None


def test_tampered_and_empty_tokens_rejected(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "the-true-secret")
    t = tok.mint_token("Smith, Pat")
    assert tok.decode_token(t + "x") is None
    assert tok.decode_token("") is None
    assert tok.decode_token(None) is None


def test_wrong_kind_rejected(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "the-true-secret")
    other = jwt.encode(
        {"provider": "X", "iss": tok.ISSUER, "kind": "something_else",
         "iat": 0, "exp": 9999999999},
        "the-true-secret", algorithm=tok.ALGORITHM)
    assert tok.decode_token(other) is None
