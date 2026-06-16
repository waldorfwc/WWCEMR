"""B1 — pending bank transactions are dropped silently at parse.

Only FINAL/posted transactions belong in a BAI2 file. A pending row
(date cell marked PENDING, or a status column = Pending) must be dropped
without being imported AND without incrementing any skipped_* counter —
it is not a "skip", it simply isn't a transaction yet.
"""
from app.services.bai2_generator import parse_csv_from_bytes, FilterOptions


def _all_skip_counters_zero(result):
    return (
        result.skipped_withdrawal == 0
        and result.skipped_modmed == 0
        and result.skipped_stripe == 0
        and result.skipped_zero == 0
        and result.skipped_duplicate_in_file == 0
        and result.skipped_always_drop == 0
    )


def test_pending_date_marker_row_is_dropped_uncounted():
    csv_bytes = (
        b"Date,Description,Amount\n"
        b"PENDING - 05/05/2026,SOMEPAYER HCCLAIMPMT,100.00\n"
        b"05/04/2026,OTHERPAYER HCCLAIMPMT,250.00\n"
    )
    out = parse_csv_from_bytes(csv_bytes, FilterOptions())

    # Only the posted row survives.
    assert len(out.transactions) == 1
    assert out.transactions[0].amount == 250.00

    # Pending was NOT counted as any kind of skip.
    assert _all_skip_counters_zero(out)


def test_pending_status_column_row_is_dropped_uncounted():
    csv_bytes = (
        b"Date,Description,Amount,Status\n"
        b"05/05/2026,SOMEPAYER HCCLAIMPMT,100.00,Pending\n"
        b"05/04/2026,OTHERPAYER HCCLAIMPMT,250.00,Posted\n"
    )
    out = parse_csv_from_bytes(csv_bytes, FilterOptions())

    assert len(out.transactions) == 1
    assert out.transactions[0].amount == 250.00
    assert _all_skip_counters_zero(out)
