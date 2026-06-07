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
    from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user, adjustment_code_reference, import_audit, groups, checklist, recall, training, google_sync, surgery, larc, billing_document, missing_charge, pellet, state_transition, idempotency, personal_task, code_helper, patient_portal, module_tier  # noqa
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    # Default groups already exist in production; the legacy seed code is
    # retained at _seed_default_groups for traceability but no longer called.
    _migrate_template_targeting()
    _seed_consent_template_from_env()
    _migrate_billing_doc_status_open_to_new()
    from app.services.larc_seed import seed_larc_device_types
    seed_larc_device_types()
    from app.services.pellet_seed import seed_pellet_dose_types
    seed_pellet_dose_types()
    # Phase B — seed default surgery facilities + procedure templates (idempotent).
    try:
        from app.services.surgery_config_seed import (
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


def _seed_consent_template_from_env():
    """One-time: if a legacy D&C template ID env var is set, register it
    in consent_templates so the matcher can use it. Idempotent.

    Seeded from DOCUSIGN_TEMPLATE_ID_DC into the docusign_template_id column.
    BoldSign templates are admin-added via the Consent Templates UI.
    """
    if not settings.docusign_template_id_dc:
        return
    from app.models.surgery import ConsentTemplate
    db = SessionLocal()
    try:
        existing = (db.query(ConsentTemplate)
                    .filter(ConsentTemplate.docusign_template_id == settings.docusign_template_id_dc)
                    .first())
        if existing:
            return
        db.add(ConsentTemplate(
            name="D&C (legacy seed)",
            docusign_template_id=settings.docusign_template_id_dc,
            procedure_match=["d&c", "dilation", "dilatation"],
            facility_match=None,
            insurance_match=[],
            is_supplemental=False,
            notes="Seeded from DOCUSIGN_TEMPLATE_ID_DC env var. Edit name / "
                  "procedure / facility match in the Consent Templates admin.",
        ))
        db.commit()
    finally:
        db.close()


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
    ]
    with engine.begin() as conn:
        for idx_name, table, cols_clause in indexes:
            if table not in existing_tables:
                continue
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols_clause})"))

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


