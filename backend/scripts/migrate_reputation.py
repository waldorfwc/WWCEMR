"""Idempotent reputation module migration: 4 tables."""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """CREATE TABLE IF NOT EXISTS reputation_profiles (
        id CHAR(36) PRIMARY KEY,
        user_email VARCHAR(200),
        display_name VARCHAR(120) NOT NULL,
        role_label VARCHAR(80),
        qr_token VARCHAR(40) NOT NULL UNIQUE,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_profiles_qr_token
       ON reputation_profiles (qr_token)""",
    """ALTER TABLE reputation_profiles
       ADD COLUMN IF NOT EXISTS location VARCHAR(40)""",
    """CREATE TABLE IF NOT EXISTS reputation_scans (
        id CHAR(36) PRIMARY KEY,
        profile_id CHAR(36) NOT NULL
            REFERENCES reputation_profiles(id) ON DELETE CASCADE,
        scanned_at TIMESTAMP NOT NULL DEFAULT NOW(),
        ip_address VARCHAR(45),
        user_agent VARCHAR(300),
        points_credited INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_scans_profile
       ON reputation_scans (profile_id, scanned_at)""",
    """CREATE TABLE IF NOT EXISTS reputation_reviews (
        id CHAR(36) PRIMARY KEY,
        profile_id CHAR(36) NOT NULL
            REFERENCES reputation_profiles(id) ON DELETE CASCADE,
        scan_id CHAR(36)
            REFERENCES reputation_scans(id) ON DELETE SET NULL,
        stars INTEGER NOT NULL,
        body TEXT,
        patient_first_name VARCHAR(80),
        patient_last_initial VARCHAR(2),
        patient_chart_number VARCHAR(20),
        patient_phone VARCHAR(20),
        consent_to_display BOOLEAN NOT NULL DEFAULT FALSE,
        approved_for_embed BOOLEAN NOT NULL DEFAULT FALSE,
        google_clicked_at TIMESTAMP,
        submitted_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_reviews_profile
       ON reputation_reviews (profile_id, submitted_at)""",
    """CREATE TABLE IF NOT EXISTS reputation_phone_challenges (
        id CHAR(36) PRIMARY KEY,
        challenge_token VARCHAR(64) NOT NULL UNIQUE,
        code_hash VARCHAR(120) NOT NULL,
        phone VARCHAR(20) NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_phone_challenges_token
       ON reputation_phone_challenges (challenge_token)""",
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
