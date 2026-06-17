"""A staff-managed surgery type: the "Surgery Name" dropdown is built from these
rows. Each type maps a name to one or more CPTs, a minor/major/office
classification, optional eligible locations, and the consent template(s) that
apply. Selecting a type during intake auto-fills the surgery.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class SurgeryType(Base):
    __tablename__ = "surgery_types"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    # [{"cpt": "58558", "description": "Hysteroscopy with D&C +/- polypectomy"}, ...]
    cpts = Column(JSON, nullable=False, default=list)
    # minor | major | office
    classification = Column(String(20), nullable=False, default="minor")
    # subset of SURGERY_FACILITY_VALUES; [] = all locations
    eligible_facilities = Column(JSON, nullable=False, default=list)
    # explicit ConsentTemplate IDs that apply to this type
    consent_template_ids = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive,
                        onupdate=now_utc_naive, nullable=False)
