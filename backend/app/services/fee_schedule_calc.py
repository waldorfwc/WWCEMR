"""Calculate the expected allowed-amount for a surgery from the fee
schedule + CCI/MPR edits."""
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import Surgery
from app.models.fee_schedule import SurgeryFeeScheduleEntry, SurgeryCciEdit


def _extract_cpts(surgery: Surgery) -> list[str]:
    out: list[str] = []
    for p in (surgery.procedures or []):
        cpt = (p.get("cpt") or "").strip()
        if cpt and cpt not in out:
            out.append(cpt)
    return out


def lookup_allowed(db: Session, insurance: str, cpt: str) -> Optional[Decimal]:
    if not insurance or not cpt:
        return None
    row = (db.query(SurgeryFeeScheduleEntry)
             .filter(SurgeryFeeScheduleEntry.insurance_name == insurance,
                     SurgeryFeeScheduleEntry.cpt_code == cpt)
             .first())
    return row.allowed_amount if row else None


def _cci_action(db: Session, cpt_a: str, cpt_b: str) -> Optional[str]:
    """Look up an explicit CCI/MPR override for the unordered pair."""
    row = (db.query(SurgeryCciEdit)
             .filter(((SurgeryCciEdit.cpt_primary   == cpt_a)
                       & (SurgeryCciEdit.cpt_secondary == cpt_b))
                     | ((SurgeryCciEdit.cpt_primary   == cpt_b)
                         & (SurgeryCciEdit.cpt_secondary == cpt_a)))
             .first())
    return row.action if row else None


def calculate_allowed_for_surgery(db: Session, surgery: Surgery) -> dict:
    """Returns:
        total_allowed: Decimal — sum after MPR + CCI
        per_cpt: list[{cpt, allowed_from_schedule, applied, reason}]
        warnings: list[str]

    Default MPR rule: the highest-allowed procedure pays at 100%; every
    additional procedure pays at 50%. Pairs marked 'blocked' drop the
    secondary entirely; 'allow_100' overrides MPR so both pay 100%.
    """
    insurance = (surgery.primary_insurance or "").strip()
    cpts = _extract_cpts(surgery)
    warnings: list[str] = []

    if not insurance:
        warnings.append("No primary insurance set on this surgery.")
    if not cpts:
        warnings.append("No procedures with CPT codes on this surgery.")

    rows: list[dict] = []
    for cpt in cpts:
        allowed = lookup_allowed(db, insurance, cpt) if insurance else None
        rows.append({
            "cpt": cpt,
            "allowed_from_schedule": allowed,
            "applied": None,
            "reason": None,
        })
        if insurance and allowed is None:
            warnings.append(f"No fee-schedule entry for {insurance} · CPT {cpt}.")

    # Sort priced rows by allowed_amount desc so MPR applies the 50% cut
    # to the lower-priced procedures (matches Medicare rule).
    priced = [r for r in rows if r["allowed_from_schedule"] is not None]
    priced.sort(key=lambda r: r["allowed_from_schedule"], reverse=True)

    # Walk in priced-desc order, applying CCI rules + MPR.
    kept: list[str] = []
    for idx, row in enumerate(priced):
        cpt = row["cpt"]

        # CCI: if any already-kept procedure blocks this one, drop it.
        blocking = next(
            (k for k in kept if _cci_action(db, k, cpt) == "blocked"),
            None,
        )
        if blocking:
            row["applied"] = Decimal("0")
            row["reason"]  = f"Blocked by CPT {blocking} (cannot be billed together)."
            warnings.append(f"CPT {cpt} dropped — blocked by {blocking}.")
            continue

        if idx == 0:
            # Highest-priced procedure always pays 100%.
            row["applied"] = row["allowed_from_schedule"]
            row["reason"]  = "Primary procedure — 100% allowed"
        else:
            # Default to MPR 50% unless an override exists.
            override = next(
                (a for a in (_cci_action(db, k, cpt) for k in kept) if a),
                None,
            )
            if override == "allow_100":
                row["applied"] = row["allowed_from_schedule"]
                row["reason"]  = "Override — paid at 100%"
            else:
                row["applied"] = (row["allowed_from_schedule"]
                                    * Decimal("0.5")).quantize(Decimal("0.01"))
                row["reason"]  = "Subsequent procedure — MPR 50%"
        kept.append(cpt)

    total = sum((r["applied"] for r in rows if isinstance(r.get("applied"), Decimal)),
                  Decimal("0"))

    # Backfill 'applied' for unpriced rows so the UI can show them.
    for r in rows:
        if r["applied"] is None:
            r["applied"] = None
            r["reason"]  = "No fee-schedule entry; skipped"

    return {
        "insurance": insurance or None,
        "total_allowed": total,
        "per_cpt": rows,
        "warnings": warnings,
    }
