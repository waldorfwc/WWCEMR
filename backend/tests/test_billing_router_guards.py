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
    "/api/waystar/status",
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


@pytest.mark.parametrize("path", CLINICAL_ROUTES)
def test_clinical_user_allowed_on_clinical_routes(clinical_client, path):
    r = clinical_client.get(path)
    assert r.status_code == 200, f"Expected 200 on {path}, got {r.status_code}: {r.text[:200]}"
