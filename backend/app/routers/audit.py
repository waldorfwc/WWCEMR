from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional

from app.database import get_db
from app.models.audit import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit_logs(
    db: Session = Depends(get_db),
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    patient_id: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if patient_id:
        q = q.filter(AuditLog.patient_id == patient_id)

    total = q.count()
    logs = q.order_by(desc(AuditLog.timestamp)).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "total": total,
        "logs": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "user_id": log.user_id,
                "user_name": log.user_name,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "patient_id": log.patient_id,
                "description": log.description,
                "status": log.status,
                "ip_address": log.ip_address,
            }
            for log in logs
        ],
    }
