"""LARC Reports endpoints: a one-shot summary of all tiles, plus per-tile
drill-down rows (JSON or CSV). Read-only (Module.LARC, Tier.VIEW)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.larc import reports as rpt
from app.utils.dt import now_utc_naive

router = APIRouter(prefix="/larc/reports", tags=["larc-reports"])


def _parse_range(from_: Optional[date], to: Optional[date]) -> tuple[date, date]:
    today = now_utc_naive().date()
    return (from_ or today.replace(day=1), to or today)


def _isoize(ins: dict) -> dict:
    out = dict(ins)
    out["prior_from"] = ins["prior_from"].isoformat()
    out["prior_to"] = ins["prior_to"].isoformat()
    return out


@router.get("/summary")
def reports_summary(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    location: Optional[str] = None,
    device_type_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
):
    df, dt = _parse_range(from_, to)
    return {
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "device_types": rpt.device_types(db),
        "workflow_funnel": rpt.workflow_funnel(db, location=location, device_type_id=device_type_id),
        "outstanding_enrollment": rpt.outstanding_enrollment(db, location=location, device_type_id=device_type_id),
        "insertions": _isoize(rpt.insertions(db, date_from=df, date_to=dt,
                                             location=location, device_type_id=device_type_id)),
        "billing_backlog": rpt.billing_backlog(db, location=location, device_type_id=device_type_id),
        "owed_patients": rpt.owed_patients(db, location=location, device_type_id=device_type_id),
        "inventory_health": rpt.inventory_health(db, location=location, device_type_id=device_type_id),
        "insertion_outcomes": rpt.insertion_outcomes(db, date_from=df, date_to=dt,
                                                     location=location, device_type_id=device_type_id),
    }


@router.get("/{tile}/rows")
def reports_rows(
    tile: str,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    location: Optional[str] = None,
    device_type_id: Optional[str] = None,
    bucket: Optional[str] = None,
    output_format: Optional[str] = Query(None, alias="format"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
):
    if tile not in rpt.VALID_TILES:
        raise HTTPException(status_code=404, detail="unknown report tile")
    df, dt = _parse_range(from_, to)
    rows = rpt.rows_for(db, tile, date_from=df, date_to=dt, location=location,
                        device_type_id=device_type_id, bucket=bucket)
    if (output_format or "").lower() == "csv":
        csv_text = rpt.rows_to_csv(rows)
        filename = f"larc-{tile}-{df.isoformat()}_{dt.isoformat()}.csv"
        return StreamingResponse(
            iter([csv_text]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return {"items": rows}
