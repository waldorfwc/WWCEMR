"""
A/R (Accounts Receivable) summary endpoint.
Aggregates data from ERA 835 imports + uploaded PrimeSuite exports.
"""

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List, Dict
from datetime import date, timedelta
from decimal import Decimal
import tempfile
import os

from app.database import get_db
from app.models.claim import Claim, ClaimStatus
from app.models.denial import Denial, DenialStatus
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.parsers.primesuite_mapper import (
    detect_primesuite_format,
    normalize_primesuite_rows,
    aggregate_ar_aging,
    is_primesuite_file,
)

router = APIRouter(prefix="/ar", tags=["ar"])


# ── ERA-based A/R from internal DB ─────────────────────────────────────────────

@router.get("/summary")
def ar_summary(db: Session = Depends(get_db)):
    """
    Compute A/R aging from ERA claims in the database.
    Buckets claims by days since date of service.
    """
    today = date.today()

    claims = db.query(Claim).filter(
        Claim.balance > 0,
        Claim.status.notin_([ClaimStatus.WRITTEN_OFF, ClaimStatus.PAID]),
    ).all()

    buckets: Dict[str, float] = {
        "0_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "91_120": 0.0,
        "120_plus": 0.0,
    }
    payer_totals: Dict[str, float] = {}
    status_totals: Dict[str, float] = {}
    oldest_dos = None
    total_outstanding = 0.0

    for c in claims:
        bal = float(c.balance or 0)
        if bal <= 0:
            continue
        total_outstanding += bal

        dos = c.date_of_service_from
        age_days = (today - dos).days if dos else 999

        if age_days <= 30:
            buckets["0_30"] += bal
        elif age_days <= 60:
            buckets["31_60"] += bal
        elif age_days <= 90:
            buckets["61_90"] += bal
        elif age_days <= 120:
            buckets["91_120"] += bal
        else:
            buckets["120_plus"] += bal

        payer = c.payer_name or "Unknown"
        payer_totals[payer] = payer_totals.get(payer, 0.0) + bal

        st = c.status.value if c.status else "pending"
        status_totals[st] = status_totals.get(st, 0.0) + bal

        if dos and (oldest_dos is None or dos < oldest_dos):
            oldest_dos = dos

    # Denial metrics
    open_denials = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN
    ).scalar() or 0
    denied_amount = db.query(func.sum(Denial.denied_amount)).filter(
        Denial.status == DenialStatus.OPEN
    ).scalar() or 0

    urgent_denials = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN,
        Denial.appeal_deadline <= today + timedelta(days=30),
        Denial.appeal_deadline >= today,
    ).scalar() or 0

    overdue_denials = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN,
        Denial.appeal_deadline < today,
    ).scalar() or 0

    # Top payers by outstanding balance
    top_payers = sorted(payer_totals.items(), key=lambda x: -x[1])[:10]

    # Collection rate
    total_billed = db.query(func.sum(Claim.billed_amount)).scalar() or 0
    total_paid = db.query(func.sum(Claim.paid_amount)).scalar() or 0
    collection_rate = round((float(total_paid) / float(total_billed) * 100), 1) if total_billed else 0.0

    return {
        "total_outstanding": round(total_outstanding, 2),
        "total_billed": round(float(total_billed), 2),
        "total_paid": round(float(total_paid), 2),
        "collection_rate_pct": collection_rate,
        "open_claim_count": len(claims),
        "aging_buckets": {k: round(v, 2) for k, v in buckets.items()},
        "payer_breakdown": [
            {"payer": p, "balance": round(b, 2)} for p, b in top_payers
        ],
        "status_breakdown": {
            k: round(v, 2) for k, v in sorted(status_totals.items(), key=lambda x: -x[1])
        },
        "denial_metrics": {
            "open_denials": open_denials,
            "denied_amount": round(float(denied_amount), 2),
            "urgent_deadlines": urgent_denials,
            "overdue": overdue_denials,
        },
        "oldest_open_dos": str(oldest_dos) if oldest_dos else None,
        "days_oldest": (today - oldest_dos).days if oldest_dos else None,
        "data_source": "era_database",
    }


@router.post("/upload-aging")
async def upload_primesuite_aging(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload a PrimeSuite AR Aging report (CSV or Excel).
    Returns normalized aging buckets and payer breakdown.
    """
    content = await file.read()
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()

    try:
        if ext in (".xlsx", ".xls"):
            import pandas as pd
            from io import BytesIO
            xf = pd.ExcelFile(BytesIO(content))
            df = xf.parse(xf.sheet_names[0])
        elif ext == ".csv":
            import pandas as pd
            from io import BytesIO
            df = pd.read_csv(BytesIO(content), on_bad_lines="skip")
        else:
            raise HTTPException(status_code=400, detail="Only CSV and Excel files supported for aging upload")

        # Normalize column names
        import re
        df.columns = [
            re.sub(r"[^a-z0-9_]", "_", c.lower().strip())
            for c in df.columns
        ]
        df = df.where(__import__("pandas").notnull(df), None)
        rows = df.to_dict("records")

        if not rows:
            return {"error": "File appears empty", "row_count": 0}

        fmt = detect_primesuite_format(list(rows[0].keys()))
        detected_fmt, normalized = normalize_primesuite_rows(rows, fmt)

        if detected_fmt == "ar_aging":
            summary = aggregate_ar_aging(normalized)
            log_action(db, "PRIMESUITE_AGING_UPLOAD", "ar", actor=current_user,
                       description=f"PrimeSuite aging upload: {filename}, {len(normalized)} rows")
            return {
                "status": "ok",
                "filename": filename,
                "detected_format": detected_fmt,
                "row_count": len(normalized),
                "summary": summary,
                "rows": normalized[:100],  # Return first 100 rows for display
            }
        else:
            # Still return what we have, just not aging-summarized
            log_action(db, "PRIMESUITE_UPLOAD", "ar", actor=current_user,
                       description=f"PrimeSuite upload: {filename}, {len(normalized)} rows, format: {detected_fmt}")
            return {
                "status": "ok",
                "filename": filename,
                "detected_format": detected_fmt or "unknown",
                "row_count": len(normalized),
                "rows": normalized[:100],
                "note": f"Detected as '{detected_fmt}' — not an aging report, so no bucket summary computed.",
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")


@router.get("/payer-performance")
def payer_performance(db: Session = Depends(get_db)):
    """
    Payer-level performance metrics: avg days to pay, denial rate, collection rate.
    """
    rows = db.query(
        Claim.payer_name,
        func.count(Claim.id).label("claim_count"),
        func.sum(Claim.billed_amount).label("total_billed"),
        func.sum(Claim.paid_amount).label("total_paid"),
        func.sum(Claim.balance).label("total_balance"),
    ).group_by(Claim.payer_name).all()

    payers = []
    for r in rows:
        billed = float(r.total_billed or 0)
        paid = float(r.total_paid or 0)
        balance = float(r.total_balance or 0)
        payers.append({
            "payer": r.payer_name or "Unknown",
            "claim_count": r.claim_count,
            "total_billed": round(billed, 2),
            "total_paid": round(paid, 2),
            "total_balance": round(balance, 2),
            "collection_rate_pct": round((paid / billed * 100), 1) if billed else 0.0,
        })

    payers.sort(key=lambda x: -x["total_billed"])
    return {"payers": payers}
