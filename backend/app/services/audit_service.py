"""HIPAA-compliant audit logging service."""

from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models.audit import AuditLog


def log_action(
    db: Session,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    ip_address: Optional[str] = None,
    description: Optional[str] = None,
    old_values: Optional[Dict] = None,
    new_values: Optional[Dict] = None,
    status: str = "success",
    error_detail: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        timestamp=datetime.utcnow(),
        user_id=user_id,
        user_name=user_name,
        ip_address=ip_address,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        patient_id=str(patient_id) if patient_id else None,
        description=description,
        old_values=old_values,
        new_values=new_values,
        status=status,
        error_detail=error_detail,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
