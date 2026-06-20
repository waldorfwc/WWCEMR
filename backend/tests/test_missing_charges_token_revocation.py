"""Provider-token revocation (per-provider token_version) + 14d TTL.

Covers: ptv embedding, verify_provider_token accept/reject by stored version,
back-compat for pre-change (no-ptv) tokens, mint_token_for_provider reading
the mapping, the 14-day TTL, the revoke endpoint, and the offboarding
auto-bump in patch_provider_mapping.
"""
import pytest

from app.config import settings
from app.models.missing_charge import ProviderUserMapping
from app.services import missing_charges_token as tok


@pytest.fixture(autouse=True)
def _stable_secret(monkeypatch):
    # Keep signing deterministic + away from any mounted env secret.
    monkeypatch.delenv("MISSING_CHARGES_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(settings, "secret_key", "test-secret-key")


def _seed_mapping(db, provider, token_version, *, email="p@wwc.com"):
    m = ProviderUserMapping(provider_name=provider, user_email=email,
                            is_active="Y", is_ignored="N",
                            token_version=token_version)
    db.add(m); db.commit(); db.refresh(m)
    return m


# ─── ptv embedding + verify accept/reject ───────────────────────────

def test_mint_embeds_ptv():
    t = tok.mint_token("Salley, Danielle", token_version=3)
    payload = tok.decode_token(t)
    assert payload["ptv"] == 3


def test_verify_accepts_matching_version_rejects_stale(db):
    prov = "Salley, Danielle"
    _seed_mapping(db, prov, token_version=1)
    # Token minted at the old version, then version bumped → rejected.
    old = tok.mint_token(prov, token_version=1)
    assert tok.verify_provider_token(db, old) is not None  # equal → accepted

    m = db.query(ProviderUserMapping).filter_by(provider_name=prov).first()
    m.token_version = 2
    db.commit()
    assert tok.verify_provider_token(db, old) is None       # ptv(1) < stored(2)

    # Fresh mint at the new version verifies again.
    fresh = tok.mint_token_for_provider(db, prov)
    assert tok.verify_provider_token(db, fresh) is not None


# ─── Back-compat: token minted before this change (no explicit ptv) ──

def test_back_compat_no_ptv_token_valid_when_stored_zero(db):
    prov = "Smith, Pat"
    # mint_token without token_version → ptv defaults to 0; no mapping → 0.
    t = tok.mint_token(prov)
    assert tok.verify_provider_token(db, t) is not None
    # Even with a mapping present at version 0.
    _seed_mapping(db, prov, token_version=0)
    assert tok.verify_provider_token(db, t) is not None


# ─── mint_token_for_provider reads the mapping version ──────────────

def test_mint_for_provider_reads_mapping_version(db):
    prov = "Jones, Lee"
    _seed_mapping(db, prov, token_version=2)
    t = tok.mint_token_for_provider(db, prov)
    assert tok.decode_token(t)["ptv"] == 2


def test_mint_for_provider_unmapped_is_zero(db):
    t = tok.mint_token_for_provider(db, "Nobody, Mapped")
    assert tok.decode_token(t)["ptv"] == 0


def test_mint_for_provider_does_not_create_mapping(db):
    """No side-effect mapping row — would poison _provider_user as a no-match."""
    tok.mint_token_for_provider(db, "Ghost, Provider")
    assert db.query(ProviderUserMapping).filter_by(
        provider_name="Ghost, Provider").first() is None


# ─── TTL = 14 days ──────────────────────────────────────────────────

def test_ttl_is_14_days():
    assert tok.TOKEN_TTL_DAYS == 14
    p = tok.decode_token(tok.mint_token("Salley, Danielle"))
    assert p["exp"] - p["iat"] == 14 * 86400


# ─── Revoke endpoint ────────────────────────────────────────────────

def test_revoke_endpoint_bumps_and_invalidates(client, db):
    prov = "Salley, Danielle"
    _seed_mapping(db, prov, token_version=0)
    good = tok.mint_token_for_provider(db, prov)   # ptv=0
    assert tok.verify_provider_token(db, good) is not None

    r = client.post(f"/api/billing/missing-charges/provider-tokens/{prov}/revoke")
    assert r.status_code == 200
    assert r.json()["token_version"] == 1

    db.expire_all()
    assert tok.verify_provider_token(db, good) is None      # ptv(0) < stored(1)

    fresh = tok.mint_token_for_provider(db, prov)            # ptv=1
    assert tok.verify_provider_token(db, fresh) is not None


def test_revoke_endpoint_creates_mapping_when_absent(client, db):
    prov = "Unmapped, Provider"
    r = client.post(f"/api/billing/missing-charges/provider-tokens/{prov}/revoke")
    assert r.status_code == 200
    assert r.json()["token_version"] == 1
    m = db.query(ProviderUserMapping).filter_by(provider_name=prov).first()
    assert m is not None and m.token_version == 1


# ─── Auto-bump on offboarding via patch ─────────────────────────────

def test_patch_is_ignored_bumps_token_version(client, db):
    prov = "Salley, Danielle"
    m = _seed_mapping(db, prov, token_version=0)
    r = client.patch(f"/api/billing/missing-charges/provider-mappings/{m.id}",
                     json={"is_ignored": True})
    assert r.status_code == 200
    assert r.json()["token_version"] == 1
    db.expire_all()
    assert db.query(ProviderUserMapping).filter_by(
        provider_name=prov).first().token_version == 1


def test_patch_is_active_false_bumps_token_version(client, db):
    prov = "Salley, Danielle"
    m = _seed_mapping(db, prov, token_version=0)
    r = client.patch(f"/api/billing/missing-charges/provider-mappings/{m.id}",
                     json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["token_version"] == 1
