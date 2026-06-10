"""Bank-recon preview CSV sweep.

Every `/api/bank-recon/preview` upload writes the raw CSV to GCS at
`bank-recon-csv/{uuid}.{ext}` plus a `{uuid}.snapshot.json`. Without a
sweep, those grow unbounded — bank-account data sitting in storage is
both a cost and a passive exposure surface.

Primary trigger: Cloud Run Job (registered in app.jobs.run as
"bank_recon_sweep") fired hourly by Cloud Scheduler. The router still
exposes a manual-trigger endpoint, but it's super-admin only — the
sweep belongs in jobs/, not in the surface area coordinators click.
(Fable design review note 6.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.bai2 import Bai2Import
from app.services.storage import blob_metadata, delete_blob, list_blob_keys


log = logging.getLogger(__name__)


def sweep_preview_csvs(
    db: Optional[Session] = None,
    *,
    ttl_hours: int = 24,
    hard_ttl_days: int = 7,
) -> dict:
    """Delete preview CSVs that have either been consumed (matching
    Bai2Import row > ttl_hours old) or that are simply older than
    hard_ttl_days regardless. Returns {inspected, deleted, ttl_hours,
    hard_ttl_days}.

    If `db` is None, opens a session for the duration of the sweep and
    closes it. That's the Cloud Run Job path.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        consumed_cutoff = now - timedelta(hours=ttl_hours)
        hard_cutoff = now - timedelta(days=hard_ttl_days)

        # Consumed previews — Bai2Import.csv_path is set; delete if generated_at
        # is old enough.
        consumed_keys = {
            r.csv_path
            for r in db.query(Bai2Import).filter(
                Bai2Import.csv_path.isnot(None),
                Bai2Import.generated_at < consumed_cutoff,
            ).all()
            if r.csv_path
        }

        deleted = 0
        inspected = 0
        for key in list_blob_keys("bank-recon-csv/"):
            inspected += 1
            if key in consumed_keys:
                if delete_blob(key):
                    deleted += 1
                continue
            meta = blob_metadata(key)
            if not meta or not meta.get("created"):
                continue
            created = meta["created"]
            if created < hard_cutoff:
                if delete_blob(key):
                    deleted += 1

        log.info("bank-recon preview CSV sweep: inspected=%d deleted=%d",
                 inspected, deleted)
        return {
            "inspected": inspected,
            "deleted": deleted,
            "ttl_hours": ttl_hours,
            "hard_ttl_days": hard_ttl_days,
        }
    finally:
        if own_session:
            db.close()
