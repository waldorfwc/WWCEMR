"""Idempotent P6 migration: surgery_messages + message_templates + seeds."""
import os
import sys
from sqlalchemy import create_engine, text

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS surgery_messages (
        id CHAR(36) PRIMARY KEY,
        surgery_id CHAR(36) NOT NULL REFERENCES surgeries(id) ON DELETE CASCADE,
        author_kind VARCHAR(20) NOT NULL,
        author_email VARCHAR(200),
        body TEXT NOT NULL,
        sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
        read_by_patient_at TIMESTAMP,
        read_by_staff_at TIMESTAMP
    )""",
    """CREATE INDEX IF NOT EXISTS ix_surgery_messages_thread
       ON surgery_messages (surgery_id, sent_at)""",
    """CREATE TABLE IF NOT EXISTS message_templates (
        id CHAR(36) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
]

SEED = [
    ("Eating/drinking before surgery",
     "Hi {{patient_name}}, you can have clear liquids up to 2 hours before "
     "your surgery on {{surgery_date}}. No solid food after midnight the "
     "night before."),
    ("Consent signing tips",
     "Hi {{patient_name}}, if you're having trouble with the consent form, "
     "please use a recent browser (Chrome/Safari) on a desktop or laptop "
     "instead of mobile. If it still won't work, call us at 240-252-2140."),
    ("FMLA processing timing",
     "Hi {{patient_name}}, we received your FMLA paperwork. We'll fill it "
     "out within 5 business days and post it to your portal."),
    ("Schedule reminder",
     "Hi {{patient_name}}, we've cleared your insurance — please log into "
     "your portal to pick a surgery date."),
    ("Post-op check-in",
     "Hi {{patient_name}}, how are you feeling after your surgery on "
     "{{surgery_date}}? Let us know if you have any concerns."),
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in SCHEMA:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
        count = conn.execute(text(
            "SELECT COUNT(*) FROM message_templates"
        )).scalar()
        if count == 0:
            import uuid
            for name, body in SEED:
                conn.execute(text(
                    "INSERT INTO message_templates (id, name, body) "
                    "VALUES (:id, :name, :body)"
                ), {"id": str(uuid.uuid4()), "name": name, "body": body})
            print(f"  ✓ seeded {len(SEED)} templates")
        else:
            print(f"  ✓ {count} templates already present — skipping seed")
    print("\nDone.")


if __name__ == "__main__":
    main()
