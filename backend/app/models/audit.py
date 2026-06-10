from sqlalchemy import Column, String, DateTime, Text, JSON
from datetime import datetime
from app.utils.dt import now_utc_naive
from app.database import Base
from app.models.guid import GUID, new_uuid


class AuditLog(Base):
    """HIPAA-compliant audit log — every access and modification recorded."""
    __tablename__ = "audit_logs"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    timestamp = Column(DateTime, default=now_utc_naive, index=True)
    user_id = Column(String(100), nullable=True)
    user_name = Column(String(200), nullable=True)
    ip_address = Column(String(50), nullable=True)

    action = Column(String(100))
    resource_type = Column(String(100))
    resource_id = Column(String(200), nullable=True)
    patient_id = Column(String(200), nullable=True)

    description = Column(Text, nullable=True)
    old_values = Column(JSON, nullable=True)
    new_values = Column(JSON, nullable=True)
    status = Column(String(20), default="success")
    error_detail = Column(Text, nullable=True)
