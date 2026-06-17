"""Surgery Reports endpoints: a one-shot summary of all tiles, plus per-tile
drill-down rows (JSON or CSV). Read-only (Tier.VIEW)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.surgery import reports as rpt
from app.utils.dt import now_utc_naive

router = APIRouter(prefix="/surgery/reports", tags=["surgery-reports"])


def _parse_range(from_: Optional[date], to_: Optional[date]) -> tuple[date, date]:
    """Default to the current month (1st → today) when omitted. FastAPI
    parses/validates the ISO date params, so a bad string yields a 422
    before we get here."""
    today = now_utc_naive().date()
    df = from_ or today.replace(day=1)
    dt = to_ or today
    return df, dt


def _isoize(completed: dict) -> dict:
    """JSON-safe the two date fields in the completed tile."""
    out = dict(completed)
    out["prior_from"] = completed["prior_from"].isoformat()
    out["prior_to"] = completed["prior_to"].isoformat()
    return out


@router.get("/summary")
def reports_summary(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    facility: Optional[str] = None,
    surgeon: Optional[str] = None,
    db: Session = Depends(get_db),
):
    df, dt = _parse_range(from_, to)
    return {
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "status_funnel": rpt.status_funnel(db, facility=facility, surgeon=surgeon),
        "not_ready": rpt.not_ready(db, facility=facility, surgeon=surgeon),
        "completed": _isoize(rpt.completed(db, date_from=df, date_to=dt,
                                           facility=facility, surgeon=surgeon)),
        "cycle_time": rpt.cycle_time(db, date_from=df, date_to=dt,
                                     facility=facility, surgeon=surgeon),
        "posting_backlog": rpt.posting_backlog(db, facility=facility, surgeon=surgeon),
        "utilization": rpt.utilization(db, date_from=df, date_to=dt, facility=facility),
    }


@router.get("/{tile}/rows")
def reports_rows(
    tile: str,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    facility: Optional[str] = None,
    surgeon: Optional[str] = None,
    bucket: Optional[str] = None,
    output_format: Optional[str] = Query(None, alias="format"),
    db: Session = Depends(get_db),
):
    if tile not in rpt.VALID_TILES:
        raise HTTPException(status_code=404, detail="unknown report tile")
    df, dt = _parse_range(from_, to)
    rows = rpt.rows_for(db, tile, date_from=df, date_to=dt, facility=facility,
                        surgeon=surgeon, bucket=bucket)
    if (output_format or "").lower() == "csv":
        csv_text = rpt.rows_to_csv(rows)
        filename = f"surgery-{tile}-{df.isoformat()}_{dt.isoformat()}.csv"
        return StreamingResponse(
            iter([csv_text]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return {"items": rows}
