"""Active-clinician picklist — active users with a non-blank NPI.

Shared by the Admin → Users clinician list (super-admin) and the LARC
enrollment pickers (Start LARC Process "Requested By", inserting-provider),
which are reachable with LARC Work. One source of truth so the two stay
identical. Front-end filters/groups by `clinician_role`.
"""
from sqlalchemy.orm import Session

from app.models.user import User


def active_clinicians(db: Session) -> list[dict]:
    """Active users with a non-blank NPI, sorted by role then name."""
    rows = (db.query(User)
              .filter(User.is_active.is_(True),
                      User.npi.isnot(None),
                      User.npi != "")
              .all())
    rows.sort(key=lambda u: (u.clinician_role or "zz", u.display_name or u.email))
    return [
        {
            "email": u.email,
            "display_name": u.display_name or u.email,
            "npi": u.npi,
            "clinician_role": u.clinician_role,
            "credential": u.credential,
        }
        for u in rows
    ]
