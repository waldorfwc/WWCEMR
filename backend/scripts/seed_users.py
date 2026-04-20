"""Idempotent user seeder. Inserts/updates users with explicit group assignments.

Safe to run multiple times. Edit the USERS list to add/change coworkers.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.user import User, UserGroup


USERS = [
    ("ocooke@waldorfwomenscare.com", UserGroup.ADMIN, "Owner"),
]


def main():
    init_db()
    db = SessionLocal()
    try:
        for email, group, display_name in USERS:
            email = email.lower().strip()
            existing = db.query(User).filter(User.email == email).first()
            if existing is None:
                db.add(User(email=email, group=group, display_name=display_name))
                print(f"  [add]    {email} -> {group.value}")
            elif existing.group != group or existing.display_name != display_name:
                existing.group = group
                existing.display_name = display_name
                print(f"  [update] {email} -> {group.value}")
            else:
                print(f"  [skip]   {email} (already {group.value})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
