"""Unified in-app operating manual sections, keyed by `module`.

Every module (device_larc, pellets, surgery, …) stores its manual content
in this single table.  A module's manual is an ordered list of sections,
each identified by a slug that is unique within that module.
"""
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class ManualSection(Base):
    """One section of a module's in-app operating manual. Keyed by `module`
    (a Module enum string) so every module shares one table + one API."""
    __tablename__ = "manual_sections"
    __table_args__ = (
        UniqueConstraint("module", "slug", name="uq_manual_module_slug"),
        Index("ix_manual_module", "module"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    module = Column(String(40), nullable=False)
    slug = Column(String(80), nullable=False)
    title = Column(String(200), nullable=False)
    body_md = Column(Text, nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive,
                        nullable=False)
    updated_by = Column(String(200), nullable=True)
