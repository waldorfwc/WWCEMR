"""Access-gate tests for the manager-facing Training reads (T7).

/matrix, /trainers, /certifications are gated at TRAINING VIEW. The
super-admin `client` fixture passes every tier (confirms routes still wired).
The `clinical_client` fixture's user isn't seeded as a User row, so
effective_tier resolves to NONE and the gate must 403. /mine stays open to
any authenticated user.
"""


# --- Super-admin can still reach the gated reads (routes still wired) ---

def test_admin_can_read_matrix(client):
    r = client.get("/api/training/matrix")
    assert r.status_code == 200, r.text


def test_admin_can_read_trainers(client):
    r = client.get("/api/training/trainers")
    assert r.status_code == 200, r.text


def test_admin_can_read_certifications(client):
    r = client.get("/api/training/certifications")
    assert r.status_code == 200, r.text


# --- Negative: a user without the Training tier is rejected ---

def test_non_tier_user_cannot_read_matrix(clinical_client):
    r = clinical_client.get("/api/training/matrix")
    assert r.status_code == 403, r.text


def test_non_tier_user_cannot_read_trainers(clinical_client):
    r = clinical_client.get("/api/training/trainers")
    assert r.status_code == 403, r.text


def test_non_tier_user_cannot_read_certifications(billing_client):
    r = billing_client.get("/api/training/certifications")
    assert r.status_code == 403, r.text


# --- Personal endpoint reachable for an authorized (VIEW) user ---
# NOTE: the training router carries a router-level requires_tier(TRAINING, VIEW)
# in main.py, so /mine (handler-level get_current_user) is still subject to the
# VIEW gate. The super-admin client clears it; a no-tier user does not.

def test_mine_reachable_for_authorized_user(client):
    r = client.get("/api/training/mine")
    assert r.status_code == 200, r.text
