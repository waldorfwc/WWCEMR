"""One-time claim data wipe — Phase 2b migration.

Deletes all legacy claim-side data so Charge Analysis imports can become the
new source of truth. Safe to keep in the repo after it's been used —
`--yes-i-am-sure` prevents accidental future runs. Touches data only, never
the schema.

Usage (from the backend/ directory):
    source venv/bin/activate
    python -m app.scripts.reset_claims_data --yes-i-am-sure
"""
import argparse
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
# Import all model modules so SQLAlchemy can resolve relationship() string
# references (e.g. Claim.patient → Patient) at mapper-configure time.
# Mirrors the import list in database.init_db().
from app.models import (  # noqa: F401
    patient, claim, payment, denial, appeal, audit, document,
    patient_directory, clinical, payment_analysis, fax_log,
    practice_config, user,
)
from app.models.claim import (
    Claim, ServiceLine, ClaimAdjustment, ServiceLineAdjustment, EraFile,
)
from app.models.denial import Denial
from app.models.appeal import Appeal
from app.models.audit import AuditLog


WIPED_RESOURCE_TYPES = {
    "claim",
    "service_line",
    "claim_adjustment",
    "service_line_adjustment",
    "denial",
    "appeal",
    "era_file",
    "charge_analysis_file",
}


def run(confirm: bool, session: Optional[Session] = None) -> Dict[str, int]:
    """Wipe claim-side data. Returns a {table_name: rows_deleted} dict."""
    if not confirm:
        raise SystemExit("Refusing to run without --yes-i-am-sure flag.")

    db = session if session is not None else SessionLocal()
    owns_db = session is None
    counts: Dict[str, int] = {}
    try:
        # Leaf-first so child rows go before their parents.
        counts["service_line_adjustments"] = (
            db.query(ServiceLineAdjustment).delete(synchronize_session=False)
        )
        counts["claim_adjustments"] = (
            db.query(ClaimAdjustment).delete(synchronize_session=False)
        )
        counts["service_lines"] = db.query(ServiceLine).delete(synchronize_session=False)
        counts["appeals"] = db.query(Appeal).delete(synchronize_session=False)
        counts["denials"] = db.query(Denial).delete(synchronize_session=False)
        counts["claims"] = db.query(Claim).delete(synchronize_session=False)
        counts["era_files"] = db.query(EraFile).delete(synchronize_session=False)
        counts["audit_log"] = (
            db.query(AuditLog)
            .filter(AuditLog.resource_type.in_(WIPED_RESOURCE_TYPES))
            .delete(synchronize_session=False)
        )
        db.commit()
    finally:
        if owns_db:
            db.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes-i-am-sure", action="store_true")
    args = parser.parse_args()
    counts = run(confirm=args.yes_i_am_sure)
    print("Wipe complete. Rows deleted per table:")
    for table, n in counts.items():
        print(f"  {table:32s} {n:>8d}")


if __name__ == "__main__":
    main()
