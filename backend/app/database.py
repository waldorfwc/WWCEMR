from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, echo=False)

# On SQLite, enable WAL mode + a sane busy-timeout so concurrent readers
# and writers don't deadlock on `database is locked` errors during long
# operations (xlsx seeds, dev reload cycles, etc.).
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _conn_record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=10000")   # 10s
        finally:
            cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user, adjustment_code_reference, import_audit, groups, checklist, recall, training, google_sync, surgery, surgery_activity, larc, larc_config, billing_document, missing_charge, pellet, pellet_config, recall_config, state_transition, idempotency, personal_task, code_helper, patient_portal, module_tier  # noqa
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    # Default groups already exist in production; the legacy seed code is
    # retained at _seed_default_groups for traceability but no longer called.
    _migrate_template_targeting()
    _migrate_billing_doc_status_open_to_new()
    _backfill_larc_assignment_device_type()
    from app.services.larc.seed import seed_larc_device_types
    seed_larc_device_types()
    from app.services.pellet.seed import seed_pellet_dose_types
    seed_pellet_dose_types()
    # Phase B — seed default surgery facilities + procedure templates (idempotent).
    try:
        from app.services.surgery.config_seed import (
            seed_default_facilities, seed_default_templates,
            seed_default_email_templates, seed_default_sms_templates,
        )
        db = SessionLocal()
        try:
            seed_default_facilities(db)
            seed_default_templates(db)
            seed_default_email_templates(db)
            seed_default_sms_templates(db)
        finally:
            db.close()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("surgery_config_seed failed")


def _backfill_larc_assignment_device_type():
    """One-time: for assignments that have a device but no device_type_id
    on the assignment row (created before the column existed), copy the
    type id over from the linked device. Idempotent."""
    insp = inspect(engine)
    if "larc_assignments" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("larc_assignments")}
    if "device_type_id" not in cols:
        return
    with engine.begin() as conn:
        # Portable correlated-subquery form (works on both PG and SQLite).
        conn.execute(text(
            "UPDATE larc_assignments "
            "SET device_type_id = ("
            "  SELECT d.device_type_id FROM larc_devices d "
            "  WHERE d.id = larc_assignments.device_id"
            ") "
            "WHERE device_type_id IS NULL AND device_id IS NOT NULL"
        ))


def _migrate_billing_doc_status_open_to_new():
    """One-time: an earlier upload endpoint set BillingDocument.status='open'
    on new uploads, but valid statuses are only ('new','in_progress','worked').
    Those rows were hidden from the default Insurance Docs view (filtered by
    [new, in_progress]). This UPDATE is idempotent once all rows are migrated."""
    insp = inspect(engine)
    if "billing_documents" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE billing_documents SET status = 'new' WHERE status = 'open'"
        ))


def _adapt_coltype_for_dialect(coltype: str, dialect: str) -> str:
    """Translate the SQLite-flavored type strings used in the migration list
    into the equivalent for the target dialect. SQLite accepts both forms
    (type affinity), Postgres is strict."""
    if dialect == "sqlite":
        return coltype
    if dialect.startswith("postgres"):
        # DATETIME isn't a Postgres type — TIMESTAMP is.
        coltype = coltype.replace("DATETIME", "TIMESTAMP")
        # Postgres BOOLEAN doesn't auto-cast integer literals in defaults.
        coltype = coltype.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
        coltype = coltype.replace("BOOLEAN DEFAULT 1", "BOOLEAN DEFAULT TRUE")
    return coltype


def _apply_lightweight_migrations():
    """Add columns to existing tables when the model gained new fields.

    SQLAlchemy's create_all() adds new TABLES but never new COLUMNS.
    We keep this list small and explicit — if this grows past a handful
    of entries, switch to Alembic.
    """
    needed = [
        # (table, column, SQL column type)
        ("surgeries", "primary_payer_id", "VARCHAR(40)"),
        ("adjustment_code_references", "wwc_notes", "TEXT"),
        ("adjustment_code_references", "wwc_notes_updated_by", "VARCHAR(255)"),
        ("adjustment_code_references", "wwc_notes_updated_at", "DATETIME"),
        # active_ar EOB-detail fields
        ("active_claims", "allowed_amount", "NUMERIC(12,2)"),
        ("active_claims", "contractual_adjustment", "NUMERIC(12,2)"),
        ("active_claims", "copay", "NUMERIC(12,2)"),
        ("active_claims", "deductible", "NUMERIC(12,2)"),
        ("active_claims", "coinsurance", "NUMERIC(12,2)"),
        ("active_claims", "patient_balance", "NUMERIC(12,2)"),
        ("active_claims", "eob_notes", "TEXT"),
        # Charge Analysis enrichment
        ("active_claims", "procedure_codes", "TEXT"),
        ("active_claims", "procedure_modifiers", "TEXT"),
        ("active_claims", "diagnosis_codes", "TEXT"),
        ("active_claims", "billable_provider_npi", "VARCHAR(20)"),
        ("active_claims", "rendering_provider_name_full", "VARCHAR(200)"),
        ("active_claims", "rendering_provider_npi", "VARCHAR(20)"),
        ("active_claims", "service_location", "VARCHAR(200)"),
        ("active_claims", "patient_dob", "DATE"),
        ("active_claims", "secondary_insurance_company", "VARCHAR(200)"),
        ("active_claims", "secondary_plan_name", "VARCHAR(200)"),
        ("active_claims", "secondary_policy_number", "VARCHAR(100)"),
        ("active_claims", "primary_plan_detail", "VARCHAR(200)"),
        ("active_claims", "enriched_at", "DATETIME"),
        ("active_claims", "service_lines_json", "TEXT"),
        # Precomputed timely-filing deadline (Fable cross-cutting audit #13).
        ("active_claims", "tf_deadline_date", "DATE"),
        ("active_claims", "tf_days_allowed", "INTEGER"),
        # Idempotency key for send-batch (Fable recalls audit C3).
        ("fax_logs", "client_request_id", "VARCHAR(80)"),
        # Persist cover text so retries can resend it (Fable recalls H6).
        ("fax_logs", "cover_text", "TEXT"),
        # Partial-day blackouts — null means whole-day (existing rows).
        ("surgery_blackout_days", "start_time", "TIME"),
        ("surgery_blackout_days", "end_time", "TIME"),
        # Soft-delete on Bai2Import (Fable design review note 13).
        ("bai2_imports", "deleted_at", "DATETIME"),
        ("bai2_imports", "deleted_by", "VARCHAR(200)"),
        # Soft-delete extended to financially-significant tables.
        ("billing_documents", "deleted_at", "DATETIME"),
        ("billing_documents", "deleted_by", "VARCHAR(200)"),
        ("claims", "deleted_at", "DATETIME"),
        ("claims", "deleted_by", "VARCHAR(200)"),
        ("larc_assignments", "deleted_at", "DATETIME"),
        ("larc_assignments", "deleted_by", "VARCHAR(200)"),
        # Practice config defaults for appeal-letter signer
        ("practice_config", "appeal_signer_name", "VARCHAR(200)"),
        ("practice_config", "appeal_signer_credentials", "VARCHAR(50)"),
        ("practice_config", "appeal_signer_title", "VARCHAR(100)"),
        # Checklist / reminders
        ("users", "practice_role", "VARCHAR(40)"),                 # ma / front_desk / billing_coding / ...
        ("users", "phone_number", "VARCHAR(20)"),                  # for SMS (phase B)
        ("users", "slack_user_id", "VARCHAR(50)"),                 # for Slack DMs
        ("users", "notify_email", "BOOLEAN DEFAULT 1"),
        ("users", "notify_slack", "BOOLEAN DEFAULT 1"),
        ("users", "notify_sms", "BOOLEAN DEFAULT 0"),
        # RBAC per-user overrides (Phase 1) — DROPPED in Phase 4 of the
        # permissions redesign; the corresponding columns are dropped by
        # scripts/migrate/drop_legacy_perms_schema.py.
        # Per-module tier model — Super Admin global flag (Phase 1 of redesign)
        ("users", "is_super_admin", "BOOLEAN DEFAULT FALSE"),
        # Checklist multi-source targeting (Phase 4)
        ("task_templates", "assigned_users", "JSON"),
        ("task_templates", "assigned_permission", "VARCHAR(80)"),
        # Audit attribution
        ("task_templates", "created_by", "VARCHAR(120)"),
        ("task_templates", "updated_by", "VARCHAR(120)"),
        # Pain point — submitter acknowledgement timestamp
        ("pain_points", "acknowledged_at", "DATETIME"),
        # Personal tasks — multi-assignee (replaces single assignee_email)
        ("personal_tasks", "assignees", "JSON"),
        # Checklist Phase 5 — flexible recurrence + Yes/No questions + manager escalation
        ("task_templates", "recurrence_kind", "VARCHAR(30)"),
        ("task_templates", "recurrence_weekdays", "JSON"),
        ("task_templates", "recurrence_days_of_month", "JSON"),
        ("task_templates", "anchor_date", "DATE"),
        ("task_templates", "interval_n", "INTEGER"),
        ("task_templates", "weekend_rule", "VARCHAR(20)"),
        ("task_templates", "question_text", "TEXT"),
        ("task_templates", "followup_kind", "VARCHAR(20) DEFAULT 'none'"),
        ("task_templates", "followup_prompt", "TEXT"),
        ("task_templates", "escalate_to_email", "VARCHAR(120)"),
        ("task_templates", "escalate_after_hours", "INTEGER DEFAULT 24"),
        ("task_instances", "answer", "VARCHAR(10)"),
        ("task_instances", "followup_count", "INTEGER"),
        ("task_instances", "followup_text", "TEXT"),
        ("task_instances", "escalation_sent_at", "DATETIME"),
        # Training & certification (Phase 6)
        ("task_templates", "requires_training", "BOOLEAN DEFAULT 1"),
        ("task_templates", "training_material_url", "TEXT"),
        ("task_templates", "expires_kind", "VARCHAR(20) DEFAULT 'never'"),
        ("task_templates", "expires_value", "INTEGER"),
        ("task_templates", "expires_on_date", "DATE"),
        # Google Workspace sync (Phase 7) — lifecycle on the User row
        ("users", "is_active", "BOOLEAN DEFAULT 1"),
        ("users", "auto_provisioned", "BOOLEAN DEFAULT 0"),
        ("users", "last_google_sync", "DATETIME"),
        # WWE history — ModMed appointment status fields (Phase 8)
        ("wwe_visits", "status", "VARCHAR(20) DEFAULT 'completed'"),
        ("wwe_visits", "is_future", "BOOLEAN DEFAULT 0"),
        ("wwe_visits", "last_seen_at", "DATETIME"),
        # Recall soft-claim fields (Phase 8) — prevents concurrent calls
        ("recall_entries", "claimed_by", "VARCHAR(200)"),
        ("recall_entries", "claimed_until", "DATETIME"),
        # Surgery block-day release-alert idempotency
        ("surgery_block_days", "release_alert_sent_at", "DATETIME"),
        # Patient-facing date-picker controls (Phase 2)
        ("surgeries", "balance_override", "BOOLEAN DEFAULT 0"),
        ("surgeries", "balance_override_by", "VARCHAR(200)"),
        ("surgeries", "balance_override_at", "DATETIME"),
        # Benefits calculator inputs (Phase 2.9)
        ("surgeries", "deductible_met", "NUMERIC(10,2)"),
        ("surgeries", "coinsurance_pct", "NUMERIC(5,2)"),
        ("surgeries", "oop_max", "NUMERIC(10,2)"),
        ("surgeries", "oop_met", "NUMERIC(10,2)"),
        # Recall — DOB direct on entry (so PatientList sheets can populate
        # without requiring patient_directory match)
        ("recall_entries", "dob", "DATE"),
        # RingCentral integration — per-user identity for click-to-dial
        ("users", "ringcentral_user_id",   "VARCHAR(40)"),
        ("users", "ringcentral_manual_override", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("users", "ringcentral_extension", "VARCHAR(20)"),
        # Phone RC will dial first for RingOut (must be a real PSTN number,
        # not an internal extension) — typically the staff member's cell
        ("users", "ringcentral_callback_number", "VARCHAR(30)"),
        # Reschedule history (patient or scheduler initiated)
        ("surgeries", "reschedule_count", "INTEGER DEFAULT 0"),
        ("surgeries", "last_rescheduled_at", "DATETIME"),
        ("surgeries", "last_rescheduled_by", "VARCHAR(80)"),
        # Post-op visit location ("office" | "telehealth")
        ("surgeries", "post_op_appt_location",     "VARCHAR(20)"),
        ("surgeries", "post_op_appt_2nd_location", "VARCHAR(20)"),
        # Pre-op lab appointment — patient self-reports on portal
        ("surgeries", "lab_appointment_date",         "DATE"),
        ("surgeries", "lab_appointment_reported_at",  "DATETIME"),
        ("surgeries", "lab_appointment_reported_by",  "VARCHAR(40)"),
        # Hospital Posting (boarding slip) editor overrides — persist
        # field-by-field corrections the coordinator made on the form.
        ("surgeries", "boarding_slip_overrides",      "JSON"),
        # Per-file send log (fax/email events) for any SurgeryFile that
        # gets shipped externally — boarding slips, modifier-22 letters,
        # clearance forms, etc.
        ("surgery_files", "send_history",             "JSON"),
        # LARC device ownership — patient_owned / wwc_owned / wwc_claimed.
        # Drives whether WWC bills insurance for the device.
        ("larc_devices", "ownership",                  "VARCHAR(20) DEFAULT 'wwc_owned'"),
        ("larc_devices", "purchasing_patient_chart",   "VARCHAR(40)"),
        ("larc_devices", "purchasing_patient_name",    "VARCHAR(200)"),
        # surgery_scheduler_notices columns (table is created via
        # create_all on a fresh DB; these add columns if the table existed
        # under an earlier shape, otherwise they're no-ops).
        ("surgery_scheduler_notices", "channels", "VARCHAR(80)"),
        ("surgery_scheduler_notices", "detail",   "TEXT"),
        # Auto-unresponsive sweep tracking (audit #13)
        ("surgeries", "last_patient_activity_at", "DATETIME"),
        ("surgeries", "auto_unresponsive_at",     "DATETIME"),
        # Pellet count-line location (Fable audit #3): finish_count
        # used a "first stock row matching expected_doses" heuristic
        # because count lines never stored which location they were
        # snapshot against. Now they do.
        ("pellet_count_lines", "location", "VARCHAR(40)"),
        # LARC enrollment-envelope auto-fax retry queue (audit #11)
        ("larc_enrollment_envelopes", "next_fax_retry_at",        "DATETIME"),
        ("larc_enrollment_envelopes", "fax_terminally_failed_at", "DATETIME"),
        # Billing (Phase 3)
        ("surgeries", "modmed_claim_number", "VARCHAR(80)"),
        ("surgeries", "billed_icd10_codes", "JSON"),
        ("surgeries", "billed_cpt_codes", "JSON"),
        ("surgeries", "billed_at", "DATETIME"),
        ("surgeries", "billed_by", "VARCHAR(120)"),
        ("surgeries", "billing_ai_notes", "TEXT"),
        # Consent envelopes: capture patient-side signing timestamp so the
        # portal can show 'awaiting countersignature' once the patient is
        # done but the surgeon/witness haven't signed yet.
        ("surgery_consent_envelopes", "patient_signed_at", "TIMESTAMP"),
        # Consent templates: CPT-code-based matching (primary). JSON list
        # — when populated, the matcher prefers it over substring keywords.
        ("consent_templates", "cpt_codes", "JSON"),
        # Secondary insurance benefit fields + card-on-file metadata
        ("surgeries", "secondary_deductible",       "NUMERIC(10,2)"),
        ("surgeries", "secondary_deductible_met",   "NUMERIC(10,2)"),
        ("surgeries", "secondary_copay",            "NUMERIC(10,2)"),
        ("surgeries", "secondary_coinsurance_pct",  "NUMERIC(5,2)"),
        ("surgeries", "secondary_oop_max",          "NUMERIC(10,2)"),
        ("surgeries", "secondary_oop_met",          "NUMERIC(10,2)"),
        ("surgeries", "card_on_file",               "BOOLEAN DEFAULT FALSE"),
        # ModMed schedule confirmation + office med pickup
        ("surgeries", "scheduled_in_modmed_by", "VARCHAR(120)"),
        ("surgeries", "office_meds_pickup_confirmed_at", "DATETIME"),
        ("surgeries", "office_meds_pickup_confirmed_by", "VARCHAR(120)"),
        # Assistant surgeon workflow
        ("surgeries", "assistant_surgeon_required", "BOOLEAN DEFAULT 0"),
        ("surgeries", "assistant_surgeon_name", "VARCHAR(200)"),
        ("surgeries", "assistant_surgeon_office_phone", "VARCHAR(40)"),
        ("surgeries", "assistant_surgeon_office_fax", "VARCHAR(40)"),
        ("surgeries", "assistant_surgeon_office_notified_at", "DATETIME"),
        ("surgeries", "assistant_surgeon_office_notified_by", "VARCHAR(120)"),
        ("surgeries", "assistant_surgeon_appt_date", "DATE"),
        ("surgeries", "assistant_surgeon_appt_confirmed_at", "DATETIME"),
        ("surgeries", "assistant_surgeon_appt_confirmed_by", "VARCHAR(120)"),
        # Intake multi-selects (surgery-intake-fields): configurable
        # clearance + device lists alongside the legacy single-string fields.
        ("surgeries", "clearance_types", "JSON"),
        ("surgeries", "device_types", "JSON"),
        # LARC: device-type category + reorder quantity (Phase 7 — office-procedure)
        ("larc_device_types", "category", "VARCHAR(20) DEFAULT 'larc'"),
        ("larc_device_types", "reorder_quantity", "INTEGER"),
        # LARC: link assignments to surgery rows (for office-procedure devices
        # assigned at scheduling time)
        ("larc_assignments", "linked_surgery_id", "CHAR(36)"),
        # Billing: missing-charges provider-mapping "ignore" flag
        ("provider_user_mappings", "is_ignored", "VARCHAR(1) DEFAULT 'N'"),
        # Pellet: per-patient recall cadence in months (default 4)
        ("pellet_patients", "recall_interval_months", "INTEGER DEFAULT 4"),
        # Pellet: deep link to the ModMed appointment record
        ("pellet_visits", "modmed_link", "TEXT"),
        # Pellet: mammogram facility info
        ("pellet_patient_mammos", "facility_name", "VARCHAR(200)"),
        ("pellet_patient_mammos", "facility_phone", "VARCHAR(40)"),
        ("pellet_patient_mammos", "facility_address", "TEXT"),
        # Pellet: preferred lab (single per patient)
        ("pellet_patients", "preferred_lab_name", "VARCHAR(200)"),
        ("pellet_patients", "preferred_lab_phone", "VARCHAR(40)"),
        ("pellet_patients", "preferred_lab_address", "TEXT"),
        # Pellet: preferred mammogram imaging facility (single per patient)
        ("pellet_patients", "preferred_mammo_facility_name", "VARCHAR(200)"),
        ("pellet_patients", "preferred_mammo_facility_phone", "VARCHAR(40)"),
        ("pellet_patients", "preferred_mammo_facility_fax",   "VARCHAR(40)"),
        ("pellet_patients", "preferred_mammo_facility_address", "TEXT"),
        # Per-mammo entry: fax column added later
        ("pellet_patient_mammos", "facility_fax", "VARCHAR(40)"),
        # Pellet: Qualgen order tracking + receipt gating
        ("pellet_receipts", "order_id",             "CHAR(36)"),
        ("pellet_receipts", "is_replacement",       "BOOLEAN DEFAULT 0"),
        ("pellet_receipts", "replaces_disposal_id", "CHAR(36)"),
        # Pellet: per-lot acquisition cost (copied from order line at verify time)
        ("pellet_lots", "unit_cost",     "NUMERIC(10,2)"),
        ("pellet_lots", "cost_per_dose", "NUMERIC(10,4)"),
        # Pellet count: witness at start (separate from finish) + scope toggle
        ("pellet_counts", "witness_user_start", "VARCHAR(120)"),
        ("pellet_counts", "scope",              "VARCHAR(30) DEFAULT 'all'"),
        # Pellet dose-type: per-location reorder threshold overrides
        ("pellet_dose_types", "reorder_thresholds_by_location", "JSON"),
        # Pellet transfers: courier chain-of-custody
        ("pellet_transfers", "courier_user",         "VARCHAR(120)"),
        ("pellet_transfers", "courier_picked_up_at", "DATETIME"),
        ("pellet_transfers", "courier_notes",        "TEXT"),
        ("pellet_transfers", "cancelled_at",         "DATETIME"),
        ("pellet_transfers", "cancelled_by",         "VARCHAR(120)"),
        # Patient portal: bind challenge codes to a purpose so a login
        # code can't authorize a payment (Fable portal audit C1).
        ("patient_portal_auth_codes", "purpose", "VARCHAR(20)"),
        # Per-surgery portal-token version for revocation
        # (Fable portal audit H5-auth).
        ("surgeries", "portal_token_version", "INTEGER DEFAULT 0 NOT NULL"),
        # Per-user JWT version for revocation on logout / suspension
        # (Fable auth audit L4).
        ("users", "token_version", "INTEGER DEFAULT 0 NOT NULL"),
        # Pellet visits: historical-import flag
        ("pellet_visits", "is_historical", "BOOLEAN DEFAULT 0"),
        # Pellet patient-level ModMed deep link (Qlik redirect from export)
        ("pellet_patients", "modmed_link", "TEXT"),
        # Optimistic locking — version_id columns
        ("surgeries",          "version_id", "INTEGER DEFAULT 1 NOT NULL"),
        ("surgery_block_days", "version_id", "INTEGER DEFAULT 1 NOT NULL"),
        ("larc_assignments",   "version_id", "INTEGER DEFAULT 1 NOT NULL"),
        ("active_claims",      "version_id", "INTEGER DEFAULT 1 NOT NULL"),
        # LARC: benefits calculator inputs (parity with Surgery's calculator)
        ("larc_assignments", "allowed_amount",       "NUMERIC(10,2)"),
        ("larc_assignments", "deductible",           "NUMERIC(10,2)"),
        ("larc_assignments", "deductible_met",       "NUMERIC(10,2)"),
        ("larc_assignments", "copay",                "NUMERIC(10,2)"),
        ("larc_assignments", "coinsurance_pct",      "NUMERIC(5,2)"),
        ("larc_assignments", "oop_max",              "NUMERIC(10,2)"),
        ("larc_assignments", "oop_met",              "NUMERIC(10,2)"),
        ("larc_assignments", "benefits_verified_at", "DATE"),
        # Billing-doc dedup: SHA-256 of uploaded bytes
        ("billing_documents", "content_hash", "VARCHAR(64)"),
        # Pellet history backfill — source row id from Smartsheet trackers,
        # used by scripts/pellet_smartsheet_history_import.py for idempotency.
        ("pellet_visits", "smartsheet_row_id", "VARCHAR(40)"),
        # The "Pellet Visit ID" column from the Smartsheet — the practice's
        # legacy visit identifier carried forward into the new system.
        ("pellet_visits", "smartsheet_visit_id", "VARCHAR(40)"),
        # Labs-not-required flag for the pellet "ready" check.
        ("pellet_patients", "labs_not_required", "BOOLEAN DEFAULT 0"),
        # LARC pharmacy enrollment — per-assignment inserting provider
        # override (falls back to PracticeConfig provider_* values when
        # blank). Used to fill the BoldSign Provider role.
        ("larc_assignments", "inserting_provider_email", "VARCHAR(200)"),
        ("larc_assignments", "inserting_provider_name",  "VARCHAR(200)"),
        ("larc_assignments", "inserting_provider_npi",   "VARCHAR(20)"),
        # LARC pharmacy enrollment — per-assignment APP override.
        ("larc_assignments", "app_email", "VARCHAR(200)"),
        ("larc_assignments", "app_name",  "VARCHAR(200)"),
        ("larc_assignments", "app_npi",   "VARCHAR(20)"),
        # Clinician identity on User — drives the LARC inserting-provider
        # / APP pickers. Empty NPI = excluded from the dropdown.
        ("users", "npi",             "VARCHAR(20)"),
        ("users", "clinician_role",  "VARCHAR(20)"),
        ("users", "credential",      "VARCHAR(10)"),
        # Track device_type on the assignment for pharmacy-order rows
        # that don't yet have a device_id. Lets the enrollment sender
        # pick the right BoldSign template before the physical device
        # arrives from the pharmacy.
        ("larc_assignments", "device_type_id", "VARCHAR(36)"),
        # LarcPharmacy.device_names / default_for_devices — drive the
        # filter + auto-default for the pharmacy picker on assignments.
        ("larc_pharmacies", "device_names",        "JSON"),
        ("larc_pharmacies", "default_for_devices", "JSON"),
        # Structured patient fields on LarcAssignment — drive enrollment-
        # form prefill. Sender prefers these over parsing patient_name.
        ("larc_assignments", "patient_first_name",     "VARCHAR(80)"),
        ("larc_assignments", "patient_middle_initial", "VARCHAR(8)"),
        ("larc_assignments", "patient_last_name",      "VARCHAR(80)"),
        ("larc_assignments", "patient_cell",           "VARCHAR(40)"),
        ("larc_assignments", "patient_address",        "VARCHAR(300)"),
        ("larc_assignments", "patient_city",           "VARCHAR(120)"),
        ("larc_assignments", "patient_state",          "VARCHAR(8)"),
        ("larc_assignments", "patient_zip",            "VARCHAR(15)"),
        ("larc_assignments", "insurance_policy_no",    "VARCHAR(80)"),
        ("larc_assignments", "insurance_group_no",     "VARCHAR(80)"),
        ("larc_assignments", "insurance_card_key",     "VARCHAR(300)"),
        ("larc_assignments", "insurance_card_filename","VARCHAR(255)"),
        ("larc_assignments", "insurance_card_content_type", "VARCHAR(100)"),
        # Patient-payment tracking — gates inventory allocation.
        ("larc_assignments", "patient_paid_at",     "DATETIME"),
        ("larc_assignments", "patient_paid_by",     "VARCHAR(200)"),
        ("larc_assignments", "patient_paid_amount", "NUMERIC(10,2)"),
        # Manager behind-schedule escalation idempotency (audit #9). Maps
        # current-step key -> ISO timestamp so the hourly sweep nags once
        # per overdue step instead of once per (retired) milestone row.
        ("surgeries", "escalation_state", "JSON"),
        # Intake-consents: curated consent template selection + manual deltas.
        ("surgeries", "consent_template_ids", "JSON"),
        ("surgeries", "consent_overrides",    "JSON"),
        # Surgery soft-delete (recoverable remove from the surgery system).
        ("surgeries", "deleted_at", "DATETIME"),
        ("surgeries", "deleted_by", "VARCHAR(200)"),
    ]
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    dialect = engine.dialect.name  # "sqlite" | "postgresql" | ...
    for table, column, coltype in needed:
        if table not in existing_tables:
            continue  # create_all just made it with the column present
        cols = {c["name"] for c in insp.get_columns(table)}
        if column in cols:
            continue
        sql_type = _adapt_coltype_for_dialect(coltype, dialect)
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))

    # Dropped columns. SQLAlchemy never drops columns on create_all(), so
    # retired fields linger. ALTER TABLE ... DROP COLUMN is supported by
    # Postgres and SQLite >= 3.35; we probe the live schema with the
    # inspector and only issue the DROP when the column actually exists, so
    # this is idempotent on both dialects and safe to re-run every boot.
    dropped = [
        # Retired Waystar status-sync columns (Waystar integration removed).
        ("active_claims", "last_status_check_at"),
        ("active_claims", "last_status_response"),
    ]
    for table, column in dropped:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if column not in cols:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        except Exception as exc:
            # Old SQLite (< 3.35) can't drop columns. Leaving the physical
            # column in place is harmless — the model no longer maps it.
            import logging
            logging.getLogger(__name__).warning(
                "Could not drop column %s.%s (likely SQLite < 3.35): %s",
                table, column, exc)

    # Composite indexes (CREATE INDEX IF NOT EXISTS — safe to re-run on every
    # boot). Add new entries here when a query starts showing up hot.
    indexes = [
        # Pellet audit log compliance queries — "every action by user X
        # over date range Y" is the DEA-style retrieval pattern.
        ("ix_pellet_audit_actor_at",
         "pellet_audit_events", "actor, at"),
        # Lot-history retrieval for inventory & write-off reports
        ("ix_pellet_audit_lot_at",
         "pellet_audit_events", "lot_id, at"),
        # Billing-doc dup detection on upload
        ("ix_billing_doc_content_hash",
         "billing_documents", "content_hash"),
        # Pellet history backfill idempotency
        ("ix_pellet_visit_smartsheet_row",
         "pellet_visits", "smartsheet_row_id"),
        # Code Helper patient roster lookup — match_patient queries by lower(last_name)
        ("ix_patients_last_name_lower",
         "patients", "lower(last_name)"),
        # Pellet dashboard — active dose-type filter runs on every load
        ("ix_pellet_dose_type_active",
         "pellet_dose_types", "is_active"),
        # Pellet dashboard — open-count "lines_remaining" uncounted lookup
        ("ix_pellet_count_line_uncounted",
         "pellet_count_lines", "count_id, counted_at"),
        # Active AR summary — tf bucket SQL aggregation by deadline date.
        # (Fable cross-cutting audit #13.)
        ("ix_active_claim_tf_deadline",
         "active_claims", "tf_deadline_date"),
    ]
    with engine.begin() as conn:
        for idx_name, table, cols_clause in indexes:
            if table not in existing_tables:
                continue
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols_clause})"))

    # Partial UNIQUE indexes — Postgres only (syntax differs in SQLite,
    # and tests use SQLite). Each entry is the DB-layer enforcement of an
    # invariant the application code also tries to maintain.
    if dialect == "postgresql":
        partial_unique_indexes = [
            # LARC: exactly one active assignment per device. The
            # application-level check (larc.py: "active assignment exists")
            # is racy under two concurrent checkout-direct calls; this
            # closes the race at the only layer that can actually enforce
            # mutual exclusion. NULL device_id is excluded so pharmacy-
            # order assignments (which have no device until receipt) don't
            # clash with each other.
            ("ix_larc_assignment_active_unique",
             "larc_assignments",
             "device_id",
             "is_active = true AND device_id IS NOT NULL"),
            # LARC: at most one in-flight checkout per device. A checkout
            # is "in-flight" when the row hasn't been resolved yet —
            # approval pending OR approved with no outcome recorded. Two
            # staff clicking "Check Out" on the same device within
            # milliseconds otherwise both pass the pending-check filter
            # and both insert a LarcCheckout row referencing the same
            # physical device. NULL device_id excluded so pharmacy-
            # order assignments (which have no device until receipt) don't
            # clash with each other.
            ("ix_larc_checkout_inflight_unique",
             "larc_checkouts",
             "device_id",
             "approval_status IN ('pending', 'approved') "
             "AND outcome IS NULL AND device_id IS NOT NULL"),
            # ERA posting: at most one EraFile per (payer_id, check_number).
            # Prevents posting the same payer remittance twice — Fable
            # billing audit H5. The in-process atomic claim closes the
            # within-worker race; this is the DB-side backstop for
            # multi-worker / retry-after-crash scenarios. Excludes
            # historical NULLs.
            ("ix_era_files_payer_check_unique",
             "era_files",
             "payer_id, check_number",
             "payer_id IS NOT NULL AND check_number IS NOT NULL"),
            # LARC: at most one live enrollment envelope per
            # (assignment, template). A double-click on Send used to
            # create two live BoldSign envelopes — patient signs both
            # → two pharmacy faxes → duplicate $300-$1,100 orders.
            # The app-level guard in send_enrollment_envelope is the
            # primary defense; this index is the DB-side backstop.
            # (Fable LARC audit C2.) Excludes terminal statuses so a
            # voided/declined/failed envelope can be re-sent.
            ("ix_larc_envelope_live_unique",
             "larc_enrollment_envelopes",
             "assignment_id, boldsign_template_id",
             "status NOT IN ('voided', 'declined', 'failed', 'faxed', 'fax_failed')"),
            # Pellet: at most one non-cancelled count per (location, day).
            # start_count has an app-level existence check, but two staff
            # clicking "Start count" within the same second both pass it
            # and create duplicate counts whose finishes then both rewrite
            # stock. (Fable audit #10.) The cross-overlap rule between
            # 'all'-scope counts and per-site counts stays in app code —
            # this index only catches exact same-day duplicates.
            ("ix_pellet_counts_one_per_day",
             "pellet_counts",
             "location, (started_at::date)",
             "status != 'cancelled'"),
            # Fax: at most one FaxLog row per (chart_number,
            # client_request_id). DB-side guarantee that a retried POST
            # (double-click, network retry) cannot re-fax the same docs.
            # The endpoint also checks application-side first to return a
            # 200 with the existing row; this index closes the race.
            # (Fable recalls/messaging audit C3.)
            ("ix_fax_logs_client_request_unique",
             "fax_logs",
             "chart_number, client_request_id",
             "client_request_id IS NOT NULL"),
            # Billing documents: at most one LIVE row per content_hash. App
            # already checks-then-acts before write, but two concurrent
            # uploads of the same scan both pass the check and both
            # insert. (Fable intake audit #7.) Excludes NULLs so old rows
            # without a hash don't block, AND excludes soft-deleted rows —
            # a deleted-then-re-uploaded file is not a live duplicate and
            # must not block the index from being created.
            ("ix_billing_documents_content_hash_unique",
             "billing_documents",
             "content_hash",
             "content_hash IS NOT NULL AND deleted_at IS NULL"),
            # NB: receipt-level dedup is enforced in app code (see
            # create_receipt) — historical data contains duplicates so a
            # DB unique index would fail to create. The race window in
            # practice is small; the app-level check is sufficient.
        ]
        for idx_name, table, cols, where in partial_unique_indexes:
            if table not in existing_tables:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {table} ({cols}) WHERE {where}"))
            except Exception as exc:
                # If existing data already violates the invariant the
                # CREATE will fail. We don't want that to block app
                # boot — log loudly so it surfaces in Cloud Run logs and
                # an operator can clean up the offending rows, then
                # rerun the boot.
                import logging
                logging.getLogger(__name__).error(
                    "Failed to create partial unique index %s on %s — "
                    "likely existing data violates uniqueness. Error: %s",
                    idx_name, table, exc)

        # CHECK constraints (Postgres only). The pellet stock floor is
        # the DEA-critical invariant: doses_on_hand can never go
        # negative for a Schedule III controlled substance. The
        # application-level _adjust_stock helper does atomic UPDATEs
        # with a WHERE doses_on_hand >= qty guard, but the DB CHECK is
        # the last line of defense if any future code path bypasses the
        # helper or a direct SQL statement is run.
        check_constraints = [
            ("pellet_stocks", "pellet_stocks_nonneg",
             "doses_on_hand >= 0"),
        ]
        for table, name, expr in check_constraints:
            if table not in existing_tables:
                continue
            try:
                with engine.begin() as conn:
                    # Postgres has no IF NOT EXISTS for ADD CONSTRAINT
                    # in supported syntax across versions, so probe
                    # information_schema first.
                    present = conn.execute(text(
                        "SELECT 1 FROM information_schema.table_constraints "
                        "WHERE table_name = :t AND constraint_name = :n"
                    ), {"t": table, "n": name}).scalar()
                    if not present:
                        conn.execute(text(
                            f"ALTER TABLE {table} "
                            f"ADD CONSTRAINT {name} CHECK ({expr})"))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to add CHECK constraint %s on %s — likely "
                    "existing data violates the invariant. Error: %s",
                    name, table, exc)

        # Make surgery_patient_auth_attempts.surgery_id nullable so the
        # failed-attempt log can record unmatched / DOB-only-match
        # attempts without charging them to a specific patient.
        # (Fable portal audit H1-router.)
        if "surgery_patient_auth_attempts" in existing_tables:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE surgery_patient_auth_attempts "
                        "ALTER COLUMN surgery_id DROP NOT NULL"))
            except Exception as exc:
                # Already nullable, or older Postgres / SQLite — idempotent.
                import logging
                logging.getLogger(__name__).debug(
                    "ALTER surgery_patient_auth_attempts.surgery_id "
                    "DROP NOT NULL skipped: %s", exc)
        # ix_pat_auth_ip_time (model-defined) is created by metadata-
        # create on fresh DBs, but a long-lived DB needs an explicit
        # CREATE INDEX IF NOT EXISTS.
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_pat_auth_ip_time "
                    "ON surgery_patient_auth_attempts (ip_address, attempted_at)"))
        except Exception:
            pass

        # Drop the (facility, block_date) uniqueness on surgery_block_days
        # so coordinators can have multiple block windows per day
        # (morning + afternoon blocks). Idempotent: skipped if the
        # constraint is already gone.
        if "surgery_block_days" in existing_tables:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE surgery_block_days "
                        "DROP CONSTRAINT IF EXISTS uq_block_facility_date"))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).debug(
                    "drop uq_block_facility_date skipped: %s", exc)

    # SUR-numbering sequence. Smartsheet used to own the numbering; now
    # the DB does. Create the sequence and prime it (once) to the highest
    # existing SUR number + 1, so we keep going where Smartsheet left
    # off. Postgres only — SQLite tests don't have surgery_number sequences.
    if dialect == "postgresql" and "surgeries" in existing_tables:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE SEQUENCE IF NOT EXISTS surgery_number_seq"))
            # Only prime when the sequence has never been touched. Once
            # is_called flips to true, nextval()'s have been issued and
            # we must NEVER setval backwards.
            is_called = conn.execute(text(
                "SELECT is_called FROM surgery_number_seq")).scalar()
            if not is_called:
                max_n = conn.execute(text(
                    "SELECT COALESCE(MAX(SUBSTRING(surgery_number FROM 4)::int), 0) "
                    "  FROM surgeries "
                    " WHERE surgery_number ~ '^SUR[0-9]+$'"
                )).scalar() or 0
                # setval(seq, v, false) → next nextval() returns v.
                conn.execute(text(
                    "SELECT setval('surgery_number_seq', :v, false)"),
                  {"v": max(int(max_n) + 1, 1)})

    # One-time backfill of active_claims.tf_deadline_date — only fills
    # rows where the column was just added (NULL) but DOS is present.
    # Bounded by open claim count (typically a few thousand). Falls back
    # silently on any error so it never blocks startup.
    # (Fable cross-cutting audit #13.)
    if "active_claims" in existing_tables:
        try:
            from app.services.timely_filing import timely_filing_info
            from app.models.active_ar import ActiveClaim as _AC
            db = SessionLocal()
            try:
                pending = (db.query(_AC)
                              .filter(_AC.tf_deadline_date.is_(None),
                                      _AC.dos.isnot(None))
                              .limit(20000)  # cap so a runaway never hangs boot
                              .all())
                touched = 0
                for ac in pending:
                    tf = timely_filing_info(ac.insurance_company, ac.dos)
                    ac.tf_deadline_date = tf["tf_deadline_date"]
                    ac.tf_days_allowed = tf["tf_days_allowed"]
                    touched += 1
                if touched:
                    db.commit()
                    import logging
                    logging.getLogger(__name__).info(
                        "Backfilled tf_deadline_date on %d active claims", touched)
            finally:
                db.close()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "tf_deadline_date backfill skipped: %s", exc)


# practice_role enum → seed group name. Used only by the one-time
# template-targeting migration below. Kept inline so we can drop
# app/services/permissions.py entirely.
_PRACTICE_ROLE_TO_GROUP = {
    "office_manager":    "Office Manager",
    "provider":          "Provider",
    "ma":                "Medical Assistant",
    "front_desk":        "Front Desk",
    "billing_coding":    "Billing — Coding",
    "billing_payments":  "Billing — Payments",
    "billing_denials":   "Billing — Denials",
    "caribcall":         "Front Desk",
}


def _migrate_template_targeting():
    """One-time: link each task template's legacy `role` value to the
    matching seed Group via task_template_groups. Idempotent — only runs
    when assigned_groups is empty for that template."""
    from app.models.checklist import TaskTemplate
    from app.models.groups import Group

    db = SessionLocal()
    try:
        groups_by_name = {g.name: g for g in db.query(Group).all()}
        if not groups_by_name:
            return  # groups not seeded yet; will run on the next boot

        templates = db.query(TaskTemplate).all()
        linked = 0
        for tmpl in templates:
            if tmpl.assigned_groups:
                continue
            if not tmpl.role:
                continue
            target_name = _PRACTICE_ROLE_TO_GROUP.get(tmpl.role)
            if not target_name:
                continue
            grp = groups_by_name.get(target_name)
            if grp:
                tmpl.assigned_groups.append(grp)
                linked += 1
        if linked:
            db.commit()
    finally:
        db.close()




