"""Authenticated walk-through of contact-attempt status on the Pellet Reports
Recall Due tile. Seeds due patients with / without recall-engine call history,
then drives the real router + permission stack (super-admin `client`) through
the summary, every bucket drill-down, and CSV. Run with -s to see the log.
"""
from datetime import datetime, timedelta, time

from app.models.pellet import PelletPatient, PelletVisit
from app.models.recall import RecallEntry
from app.services.pellet.recall_sync import PELLET_RECALL_TYPE
from app.utils.dt import now_utc_naive


def _patient(db, chart, name, **kw):
    p = PelletPatient(chart_number=chart, patient_name=name, status="active",
                      recall_interval_months=4, **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _billed_visit(db, p, days_ago, location="white_plains"):
    now = now_utc_naive()
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                    location=location, provider="Cooke, Aryian, MD",
                    inserted_at=now - timedelta(days=days_ago))
    db.add(v); db.commit(); db.refresh(v)
    return v


def _recall_entry(db, chart, name, *, attempts, outcome=None, worked_by=None):
    now = now_utc_naive()
    db.add(RecallEntry(chart_number=chart, patient_name=name,
                       recall_type=PELLET_RECALL_TYPE, source="pellet", status="active",
                       attempts=attempts, last_outcome=outcome, last_worked_by=worked_by,
                       last_attempt_at=(now - timedelta(days=1)) if attempts else None))
    db.commit()


def test_contact_status_walkthrough(client, db, capsys):
    # interval 4mo -> due = last insertion + 120 days.
    # overdue: inserted 200d ago (due ~80d ago). due_soon: inserted 100d ago (due ~+20d).
    p1 = _patient(db, "WC1", "Adams, Mary"); _billed_visit(db, p1, 200)   # overdue
    p2 = _patient(db, "WC2", "Brown, Sue");  _billed_visit(db, p2, 200)   # overdue
    p3 = _patient(db, "WC3", "Cole, Ann");   _billed_visit(db, p3, 100)   # due soon
    p4 = _patient(db, "WC4", "Diaz, Lou");   _billed_visit(db, p4, 100)   # due soon
    p5 = _patient(db, "WC5", "Eaton, Kay");  _billed_visit(db, p5, 10)    # not due (excluded)

    # Call history: P1 + P3 contacted; P4 has an entry but 0 attempts; P2 has none.
    _recall_entry(db, "WC1", "Adams, Mary", attempts=3, outcome="Left voicemail",
                  worked_by="reception@wwc.com")
    _recall_entry(db, "WC3", "Cole, Ann", attempts=1, outcome="No answer",
                  worked_by="reception@wwc.com")
    _recall_entry(db, "WC4", "Diaz, Lou", attempts=0)

    log = []

    # 1. Summary — contact-status counts on the recall_due tile.
    body = client.get("/api/pellets/reports/summary").json()
    rd = body["recall_due"]
    assert rd["total"] == 4 and rd["overdue"] == 2 and rd["due_soon"] == 2
    assert rd["contacted"] == 2 and rd["not_contacted"] == 2
    log.append(f"1. SUMMARY recall_due: total={rd['total']} "
               f"(overdue {rd['overdue']} / due_soon {rd['due_soon']}) | "
               f"contacted={rd['contacted']} not_contacted={rd['not_contacted']}")

    def rows(bucket=None):
        url = "/api/pellets/reports/recall_due/rows"
        if bucket:
            url += f"?bucket={bucket}"
        r = client.get(url)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    # 2. Full drill — every row carries the contact columns.
    allrows = rows()
    assert len(allrows) == 4
    cols = {"attempts", "last_outcome", "last_attempt_at", "last_worked_by"}
    assert all(cols <= set(r) for r in allrows)
    a = next(r for r in allrows if r["chart_number"] == "WC1")
    assert a["attempts"] == 3 and a["last_outcome"] == "Left voicemail"
    assert a["last_worked_by"] == "reception@wwc.com" and a["last_attempt_at"]
    log.append(f"2. drill recall_due (all) -> {len(allrows)} rows; "
               f"WC1: {a['attempts']} attempts, last '{a['last_outcome']}' "
               f"on {a['last_attempt_at']} by {a['last_worked_by']}")

    # 3. Contact-status bucket filters.
    nc = rows("not_contacted")
    c = rows("contacted")
    assert {r["chart_number"] for r in nc} == {"WC2", "WC4"}
    assert {r["chart_number"] for r in c} == {"WC1", "WC3"}
    assert all(r["attempts"] == 0 for r in nc) and all(r["attempts"] >= 1 for r in c)
    log.append(f"3. bucket=not_contacted -> {sorted(r['chart_number'] for r in nc)} "
               f"(all 0 attempts); bucket=contacted -> {sorted(r['chart_number'] for r in c)}")

    # 4. Due-ness buckets still work alongside contact buckets.
    od = rows("overdue"); ds = rows("due_soon")
    assert {r["chart_number"] for r in od} == {"WC1", "WC2"}
    assert {r["chart_number"] for r in ds} == {"WC3", "WC4"}
    log.append(f"4. bucket=overdue -> {sorted(r['chart_number'] for r in od)}; "
               f"bucket=due_soon -> {sorted(r['chart_number'] for r in ds)}")

    # 5. CSV export of the not-yet-contacted worklist.
    csv_resp = client.get("/api/pellets/reports/recall_due/rows?bucket=not_contacted&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    header = csv_resp.text.splitlines()[0]
    assert "attempts" in header and "last_outcome" in header and "chart_number" in header
    data_lines = [l for l in csv_resp.text.splitlines()[1:] if l.strip()]
    assert len(data_lines) == 2
    log.append(f"5. CSV (not_contacted) -> {csv_resp.headers['content-type'].split(';')[0]}, "
               f"{len(data_lines)} rows, header has attempts/last_outcome")

    with capsys.disabled():
        print("\n  === Recall Due — contact-status walk-through (authenticated) ===")
        for line in log:
            print("   " + line)
        print("   === all assertions passed ===\n")
