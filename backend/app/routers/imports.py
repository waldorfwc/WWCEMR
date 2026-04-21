"""Import routing — file history endpoint (upload moved to per-format routers in Phase 2c)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/era-files")
def list_era_files(db: Session = Depends(get_db)):
    from app.models.claim import EraFile
    files = db.query(EraFile).order_by(desc(EraFile.imported_at)).limit(100).all()
    return [
        {
            "id": str(f.id),
            "filename": f.filename,
            "payer_name": f.payer_name,
            "check_number": f.check_number,
            "check_date": str(f.check_date) if f.check_date else None,
            "check_amount": float(f.check_amount or 0),
            "transaction_count": f.transaction_count,
            "status": f.status,
            "imported_at": f.imported_at.isoformat() if f.imported_at else None,
        }
        for f in files
    ]
