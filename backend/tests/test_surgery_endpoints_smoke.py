"""Surgery endpoint smoke regression suite.

Cheap coverage that catches route/import/permission breakage:

  * Reachability + no-500: the main surgery GET/list endpoints answer
    200 (or a documented 4xx) with the super-admin `client` — never 500.
  * Auth gating: representative endpoints across the tier ladder (VIEW /
    WORK / MANAGE / SUPER_ADMIN) reject an under-privileged caller with
    403.

Auth-gating mechanism: requires_tier / requires_super_admin resolve the
caller's tier by DB lookup on their email (app/permissions/resolver.py).
The `clinical_client` fixture authenticates as clinician@... but that
email is NOT seeded as a User row, so effective_tier() resolves to
Tier.NONE → every gated surgery endpoint returns 403. That makes it a
clean "insufficient tier" probe without hand-building grants.

Every path below was verified to exist in app/routers/surgery.py,
surgery_config.py, or surgery_reports.py before being asserted on.
"""
from datetime import date, time, timedelta

import pytest

from app.models.surgery import Surgery, BlockDay


# ─── Reachability: every listed GET must answer 200, never 500 ──────────

# (path, expected_status). All are super-admin-reachable read endpoints
# that need no seeded data to return 200. Paths verified against the
# routers; tiers are all <= SUPER_ADMIN so the super-admin client passes.
_GET_OK_PATHS = [
    "/api/surgery",                       # list (surgery.py:831 GET "")
    "/api/surgery/dashboard",             # surgery.py:342
    "/api/surgery/calendar",              # surgery.py:655
    "/api/surgery/picklists",             # surgery.py:1564
    "/api/surgery/picklists/facilities",  # surgery_config.py:444
    "/api/surgery/config",                # surgery_config.py:292
    "/api/surgery/admin/blackouts",       # surgery.py:4256
    "/api/surgery/admin/surgery-types",   # surgery.py:1587 (MANAGE)
    "/api/surgery/admin/block-schedules", # surgery.py:2967
    "/api/surgery/reports/summary",       # surgery_reports.py:37
    "/api/surgery/todos",                 # surgery.py:1721
    "/api/surgery/deleted",               # surgery.py:1689
]


@pytest.mark.parametrize("path", _GET_OK_PATHS)
def test_surgery_get_endpoints_reachable_no_500(client, path):
    r = client.get(path)
    assert r.status_code != 500, f"{path} → 500: {r.text}"
    assert r.status_code == 200, f"{path} → {r.status_code}: {r.text}"


def test_calendar_with_range_no_500(client):
    # /calendar is the OR-board view; exercise it with an explicit window
    # so a query-param regression surfaces here too.
    today = date.today()
    r = client.get("/api/surgery/calendar", params={
        "from": today.isoformat(),
        "to": (today + timedelta(days=30)).isoformat(),
    })
    assert r.status_code == 200, r.text


def test_detail_endpoint_reachable(client, db):
    # /{surgery_id} is the most-hit read path. Seed one row so we exercise
    # the real serializer (_surgery_dict) rather than only the 404 branch.
    s = Surgery(chart_number="SMOKE1", patient_name="Smoke, Test",
                status="new", selected_facility="medstar",
                eligible_facilities=["medstar"])
    db.add(s); db.commit(); db.refresh(s)
    r = client.get(f"/api/surgery/{s.id}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == str(s.id)


def test_detail_unknown_id_is_404_not_500(client):
    r = client.get("/api/surgery/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404, r.text


# ─── Auth gating: under-privileged caller is denied ─────────────────────
#
# clinical_client → email not seeded → effective_tier == NONE → 403.
# One representative endpoint per tier on the surgery module ladder.

def _seed_surgery(db):
    s = Surgery(chart_number="GATE1", patient_name="Gate, Test",
                status="new", selected_facility="medstar",
                eligible_facilities=["medstar"],
                procedure_classification="robotic_180")
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_gate_view_read_denied_for_low_tier(clinical_client, db):
    # VIEW-tier read: the surgery list.
    r = clinical_client.get("/api/surgery")
    assert r.status_code == 403, r.text


def test_gate_work_mutation_denied_for_low_tier(clinical_client, db):
    # WORK-tier mutation: coordinator schedule. Should 403 on the tier
    # gate before it ever looks at the (here garbage) body.
    s = _seed_surgery(db)
    r = clinical_client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": "00000000-0000-0000-0000-000000000000",
        "start_time": "07:00",
    })
    assert r.status_code == 403, r.text


def test_gate_manage_mutation_denied_for_low_tier(clinical_client, db):
    # MANAGE-tier mutation: create a blackout day.
    r = clinical_client.post("/api/surgery/admin/blackouts", json={
        "blackout_date": (date.today() + timedelta(days=10)).isoformat(),
        "scope": "office",
        "reason": "holiday",
    })
    assert r.status_code == 403, r.text


def test_gate_super_admin_endpoint_denied_for_low_tier(clinical_client, db):
    # SUPER_ADMIN endpoint: run-escalations sweep.
    r = clinical_client.post("/api/surgery/admin/run-escalations", json={})
    assert r.status_code == 403, r.text


# Positive control: the super-admin `client` passes the same gates the
# low-tier caller is denied on, proving the 403s above are about tier and
# not a broken route. (Schedule needs real capacity to fully succeed, so
# we only assert it is NOT a 403 — the gate let us through.)
def test_super_admin_passes_work_gate(client, db):
    s = _seed_surgery(db)
    bd = BlockDay(facility="medstar",
                  block_date=date.today() + timedelta(days=21),
                  block_kind="robotic_180",
                  start_time=time(7, 0), end_time=time(15, 0))
    db.add(bd); db.commit(); db.refresh(bd)
    r = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id),
        "start_time": "07:00",
        "duration_minutes": 180,
    })
    assert r.status_code != 403, r.text
    assert r.status_code == 200, r.text
