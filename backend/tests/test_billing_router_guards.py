"""Every billing-only router must 403 a clinical user.

Fax routers are clinical-accessible (document workflow), so they go in CLINICAL_ROUTES.
"""
import pytest

BILLING_ROUTES = [
    "/api/claims",
    "/api/claims/summary",
    "/api/denials",
    "/api/denials/summary",
    "/api/appeals",
    "/api/audit",
    "/api/ar/summary",
    "/api/imports/era-files",
    "/api/dashboard/summary",
]


CLINICAL_FAX_ROUTES = [
    "/api/fax/recent",
    "/api/fax-log",
]


@pytest.mark.parametrize("path", BILLING_ROUTES)
def test_clinical_user_forbidden_on_billing_routes(clinical_client, path):
    r = clinical_client.get(path)
    assert r.status_code == 403, f"Expected 403 on {path}, got {r.status_code}: {r.text[:200]}"


CLINICAL_ROUTES = [
    "/api/auth/me",
    "/api/documents/index/status",
] + CLINICAL_FAX_ROUTES


def _grant_clinical_chart_view(db):
    """Persist the clinical user with a real Chart:View grant.

    The fax + document routers are gated by
    `requires_tier(Module.CHART, Tier.VIEW)`, which resolves the caller's
    tier from persisted rows (super-admin → per-user override → group → none).
    The `clinical_client` fixture only injects a user *dict*; with no backing
    rows the resolver returns NONE and the gate 403s. Seeding a User row plus
    a Chart:View override models an actual clinical staffer so the gate
    correctly admits them. (Per-module permissions redesign — fixture drift,
    not a product regression: the gate itself is working as intended.)
    """
    from app.models.user import User, UserGroup
    from app.models.module_tier import UserModuleOverride
    from app.permissions.catalog import Module, Tier
    from tests.conftest import CLINICAL_USER

    email = CLINICAL_USER["email"]
    if db.query(User).filter(User.email == email).first() is None:
        db.add(User(email=email, display_name=CLINICAL_USER["name"],
                    group=UserGroup.CLINICAL))
        db.add(UserModuleOverride(
            user_email=email, module=Module.CHART.value, tier=int(Tier.VIEW),
            added_by="test"))
        db.commit()


@pytest.mark.parametrize("path", CLINICAL_ROUTES)
def test_clinical_user_allowed_on_clinical_routes(clinical_client, db, path):
    _grant_clinical_chart_view(db)
    r = clinical_client.get(path)
    assert r.status_code == 200, f"Expected 200 on {path}, got {r.status_code}: {r.text[:200]}"
