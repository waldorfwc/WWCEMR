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
    from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user, adjustment_code_reference, import_audit, groups, checklist, recall, training, google_sync, surgery, larc, billing_document, missing_charge, pellet, state_transition, idempotency, personal_task  # noqa
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    _seed_default_groups()
    _migrate_template_targeting()
    _seed_consent_template_from_env()
    from app.services.larc_seed import seed_larc_device_types
    seed_larc_device_types()
    from app.services.pellet_seed import seed_pellet_dose_types
    seed_pellet_dose_types()


def _seed_consent_template_from_env():
    """One-time: if a D&C DocuSign template ID is in the env, register it
    in consent_templates so the matcher can use it. Idempotent."""
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
        # RBAC per-user overrides (Phase 1)
        ("users", "permissions_extra", "JSON"),
        ("users", "permissions_revoked", "JSON"),
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
        # Billing (Phase 3)
        ("surgeries", "modmed_claim_number", "VARCHAR(80)"),
        ("surgeries", "billed_icd10_codes", "JSON"),
        ("surgeries", "billed_cpt_codes", "JSON"),
        ("surgeries", "billed_at", "DATETIME"),
        ("surgeries", "billed_by", "VARCHAR(120)"),
        ("surgeries", "billing_ai_notes", "TEXT"),
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
    ]
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    for table, column, coltype in needed:
        if table not in existing_tables:
            continue  # create_all just made it with the column present
        cols = {c["name"] for c in insp.get_columns(table)}
        if column in cols:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))

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
    ]
    with engine.begin() as conn:
        for idx_name, table, cols_clause in indexes:
            if table not in existing_tables:
                continue
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols_clause})"))


def _migrate_template_targeting():
    """One-time: link each task template's legacy `role` value to the
    matching seed Group via task_template_groups. Idempotent — only runs
    when assigned_groups is empty for that template."""
    from app.models.checklist import TaskTemplate
    from app.models.groups import Group
    from app.services.permissions import PRACTICE_ROLE_TO_GROUP

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
            target_name = PRACTICE_ROLE_TO_GROUP.get(tmpl.role)
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


def _seed_default_groups():
    """Idempotently seed RBAC default groups and migrate existing users.

    Safe to call on every boot. On the first run: creates the 9 default
    groups, sets their permission rows, and assigns existing users into
    groups matching their legacy `group` + `practice_role`. On subsequent
    runs: only ensures the seeded groups exist; never touches user
    memberships (admin owns those once migration has happened).
    """
    from app.models.groups import Group, GroupPermission
    from app.models.user import User
    from app.services.permissions import (
        DEFAULT_GROUPS, legacy_groups_for_user,
    )

    db = SessionLocal()
    try:
        existing_by_name = {g.name: g for g in db.query(Group).all()}
        first_time = len(existing_by_name) == 0

        for spec in DEFAULT_GROUPS:
            grp = existing_by_name.get(spec["name"])
            if grp is None:
                grp = Group(
                    name=spec["name"],
                    description=spec["description"],
                    system_protected=spec["system_protected"],
                )
                db.add(grp)
                db.flush()  # get id
                for perm_str in spec["permissions"]:
                    db.add(GroupPermission(group_id=grp.id, permission=perm_str,
                                           granted_by="system:seed"))
                existing_by_name[spec["name"]] = grp
            elif grp.system_protected:
                # System-protected groups get any missing spec permissions
                # backfilled on every boot. This is how new permissions
                # added to the catalog (e.g. training:authorize) reach
                # the Admin / Office Manager seed groups without manual
                # intervention. Custom (non-protected) groups are left
                # alone — admins manage their permissions in-app.
                existing_perms = {gp.permission for gp in grp.permissions}
                for perm_str in spec["permissions"]:
                    if perm_str not in existing_perms:
                        db.add(GroupPermission(group_id=grp.id, permission=perm_str,
                                               granted_by="system:seed-backfill"))

        db.commit()

        if first_time:
            # One-time migration: assign each existing user to seed groups
            # that match their legacy fields. Skip users with no legacy data.
            for u in db.query(User).all():
                legacy_group = u.group.value if hasattr(u.group, "value") else u.group
                wanted_names = legacy_groups_for_user(legacy_group, u.practice_role)
                for gname in wanted_names:
                    grp = existing_by_name.get(gname)
                    if grp and grp not in u.groups:
                        u.groups.append(grp)
            db.commit()
    finally:
        db.close()
