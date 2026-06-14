"""Access-gate tests for /api/admin/reputation (MK1).

Reads are gated at REPUTATION VIEW, writes at REPUTATION MANAGE. The
super-admin `client` fixture passes every tier, so it confirms the routes
are still wired and resolve (not 5xx). The `clinical_client` fixture's user
is not seeded as a User row, so effective_tier resolves to NONE and the
gate must 403 — that is the real tightening assertion.
"""


# --- Super-admin can still reach the gated reads (routes still wired) ---

def test_admin_can_read_profiles(client):
    r = client.get("/api/admin/reputation/profiles")
    assert r.status_code == 200, r.text


def test_admin_can_read_leaderboard(client):
    r = client.get("/api/admin/reputation/leaderboard")
    assert r.status_code == 200, r.text


def test_admin_can_read_reviews(client):
    r = client.get("/api/admin/reputation/reviews")
    assert r.status_code == 200, r.text


# --- Negative: a user without the Reputation tier is rejected ---

def test_non_tier_user_cannot_read_reviews(clinical_client):
    r = clinical_client.get("/api/admin/reputation/reviews")
    assert r.status_code == 403, r.text


def test_non_tier_user_cannot_read_profiles(clinical_client):
    r = clinical_client.get("/api/admin/reputation/profiles")
    assert r.status_code == 403, r.text


def test_non_tier_user_cannot_read_leaderboard(clinical_client):
    r = clinical_client.get("/api/admin/reputation/leaderboard")
    assert r.status_code == 403, r.text


def test_non_tier_user_cannot_create_profile(clinical_client):
    r = clinical_client.post("/api/admin/reputation/profiles",
                             json={"display_name": "Nope"})
    assert r.status_code == 403, r.text


def test_non_tier_user_cannot_patch_review(billing_client):
    # rid is irrelevant — the tier gate fires before the handler body.
    r = billing_client.patch("/api/admin/reputation/reviews/whatever",
                             json={"approved_for_embed": True})
    assert r.status_code == 403, r.text
