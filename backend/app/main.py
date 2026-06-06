# Load .env into os.environ before any module reads it. Pydantic Settings
# parses .env into its own object but doesn't push values back to the env;
# integrations like ringcentral_client read os.environ directly.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import init_db
from app.routers import imports, claims, patients, denials, appeals, eob, audit
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, admin_groups, service_lines, claim_adjustments, service_line_adjustments, charge_imports, claim_id_bootstrap, era_posting, adjustment_codes, transaction_detail_imports, active_ar, active_ar_filter_presets, bank_recon, checklist, recalls, recall_filter_presets, training, surgery, surgery_config, patient_surgery, patient_portal, docusign as docusign_router, boldsign, consent_templates, surgery_filter_presets, larc, pellet, billing_documents, missing_charges, personal_tasks, code_helper, insurance_contacts
from app.routers import google_sync as google_sync_router
from app.routers import admin_tiers
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.routers import portal_preview
from app.routers import stripe_payments
from app.routers import surgery_messages
from app.routers import message_templates
from app.routers import reputation_public, reputation_admin
from app.routers import fee_schedule as fee_schedule_router
from app.models import patient_email as _patient_email_models  # noqa: F401
from app.models import patient_sms as _patient_sms_models  # noqa: F401
from app.models import fee_schedule as _fee_schedule_models  # noqa: F401

# RBAC guards. Every router below gates on a specific permission, computed
# from the user's group memberships + per-user extras/revokes (see
# app/services/permissions.py). Specific routers gate on tighter permissions
# (audit:read, bankrecon:read, report:financial, user:manage).
BILLING_READ        = [Depends(auth.require_permission("claim:read"))]
USER_MANAGE         = [Depends(auth.require_permission("user:manage"))]
AUDIT_READ          = [Depends(auth.require_permission("audit:read"))]
BANKRECON_READ      = [Depends(auth.require_permission("bankrecon:read"))]
REPORT_FINANCIAL    = [Depends(auth.require_permission("report:financial"))]
PATIENT_READ        = [Depends(auth.require_permission("patient:read"))]
DOCUMENT_READ       = [Depends(auth.require_permission("document:read"))]
CHART_READ          = [Depends(auth.require_permission("chart:read"))]
INTAKE_READ         = [Depends(auth.require_permission("intake:read"))]
FAX_READ            = [Depends(auth.require_permission("fax:read"))]
# Authentication only — requires a valid session (cookie or bearer) but no
# specific permission. Use sparingly; only for routers that are truly
# permission-less (auth/me, etc.). PHI routers should pick a *_READ above
# so role-less accounts (shared mailboxes, ghost auto-provisioned users)
# can't read patient data.
AUTH_ONLY           = [Depends(auth.get_current_user)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.services.fax_poller import start_scheduler
    sched = start_scheduler()
    # One-time route permission audit — surfaces any endpoint missing a
    # require_permission guard. Logged at WARNING so it's visible without
    # being fatal during development.
    try:
        from app.services.route_perm_catalog import audit_routes
        audit_routes(app)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("route_perm_catalog failed")
    try:
        yield
    finally:
        sched.shutdown(wait=False)


app = FastAPI(
    title="GW Migration System",
    description="Greenway PrimeSuite migration — patient charts, documents, ERA 835 payment posting, denial management.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://100.114.128.14:3000",
        "http://wwcs-mac-mini.tailb1a9cf.ts.net:3000",
        "https://gw.waldorfwomenscare.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Optimistic-locking exception → HTTP 409.
# Raised by SQLAlchemy when version_id_col detects a stale write (another
# session committed an update between our SELECT and UPDATE). Surfaces as
# a friendly 409 so the client can refetch + retry instead of crashing.
from sqlalchemy.orm.exc import StaleDataError
from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(StaleDataError)
async def _stale_data_handler(request: Request, exc: StaleDataError):
    return JSONResponse(
        status_code=409,
        content={
            "detail": "This record was modified by another user since you "
                      "opened it. Refresh and try again.",
            "error": "stale_data",
        },
    )

app.include_router(imports.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(claims.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(service_lines.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(claim_adjustments.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(service_line_adjustments.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(charge_imports.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(claim_id_bootstrap.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(era_posting.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(patients.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(denials.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(appeals.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(eob.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(audit.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.AUDIT_LOG, Tier.VIEW))])
app.include_router(waystar.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(ar.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(documents.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(intake.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(chart.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(fax.router, prefix="/api", dependencies=FAX_READ)
app.include_router(auth.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api", dependencies=REPORT_FINANCIAL)
app.include_router(fax_batch.router, prefix="/api", dependencies=FAX_READ)
app.include_router(fax_batch.log_router, prefix="/api", dependencies=FAX_READ)
app.include_router(admin_users.router, prefix="/api", dependencies=USER_MANAGE)
app.include_router(admin_groups.router, prefix="/api", dependencies=USER_MANAGE)
# admin_tiers does its own per-route auth (Super Admin / per-module Admin),
# so it intentionally has no router-level dependency.
app.include_router(admin_tiers.router, prefix="/api")
app.include_router(adjustment_codes.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(transaction_detail_imports.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(active_ar.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(active_ar_filter_presets.router, prefix="/api", dependencies=BILLING_READ)
app.include_router(bank_recon.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.BANK_RECON, Tier.VIEW))])
# Checklist is open to all authenticated users (each user works their own list)
app.include_router(checklist.router, prefix="/api")
# Recalls require recall:work / recall:manage — gates inside each handler
app.include_router(recalls.router, prefix="/api")
app.include_router(recall_filter_presets.router, prefix="/api")
# Training: router-level VIEW (everyone sees what's available), handlers
# escalate to MANAGE for authorize/revoke/force-acknowledge.
app.include_router(training.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.TRAINING, Tier.VIEW))])
# Surgery config admin (must come before surgery.router — /config would match /{surgery_id})
app.include_router(surgery_config.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
# Surgery fee schedule + CCI/MPR admin + per-surgery calculator
# (Must come BEFORE surgery.router — /fee-schedule would match /{surgery_id})
app.include_router(fee_schedule_router.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
# Surgery scheduling — handlers gate by per-endpoint requires_tier on SURGERY
app.include_router(surgery.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
# Patient-facing date picker — public, soft-auth via DOB + last 4 of phone
app.include_router(patient_surgery.router, prefix="/api")
# Patient portal — durable session-based sign-in (DOB + last4 -> SMS challenge -> JWT)
app.include_router(patient_portal.router, prefix="/api")
# Staff portal-preview token — issues a short-lived read-only portal JWT for coordinators
app.include_router(portal_preview.router)
# DocuSign Connect webhook — no auth (DocuSign POSTs from outside; verified via HMAC)
app.include_router(docusign_router.router, prefix="/api")
# BoldSign Connect webhook — no auth (BoldSign POSTs from outside;
# verified via HMAC). Lives alongside DocuSign during the provider migration.
app.include_router(boldsign.router, prefix="/api")
# Consent template admin — gated by per-endpoint Surgery:Manage
app.include_router(consent_templates.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
# User-scoped saved filter presets for the surgery dashboard
app.include_router(surgery_filter_presets.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
# LARC device inventory + tracking
app.include_router(larc.router, prefix="/api")
# Pellet inventory + receiving + DEA-compliant audit
app.include_router(pellet.router, prefix="/api")
# Billing — Insurance Documents (Paper EOBs, patient payments, letters)
app.include_router(billing_documents.router, prefix="/api")
# Billing — Missing Charges (ModMed report ingest + workflow)
app.include_router(missing_charges.router, prefix="/api")
# Billing — Insurance Contacts (company directory: claims links, phones, notes)
app.include_router(insurance_contacts.router, prefix="/api")
app.include_router(personal_tasks.router,  prefix="/api")
# Code Helper — AI-assisted CPT + ICD-10 coding; handlers gate by get_current_user
app.include_router(code_helper.router, prefix="/api")
# Google Workspace sync — admin-only
app.include_router(google_sync_router.router, prefix="/api", dependencies=USER_MANAGE)
# Stripe payments — coordinator endpoints + patient self-service + webhook
app.include_router(stripe_payments.router, prefix="/api")
# Staff messaging — per-surgery thread + unread inbox; router carries /api/staff prefix
app.include_router(surgery_messages.router)
# Message templates — staff-managed canned replies with variable substitution
app.include_router(message_templates.router)
# Reputation — public QR-gated review form endpoints (no auth; token is the boundary)
app.include_router(reputation_public.router)
app.include_router(reputation_public.embed_router)
app.include_router(reputation_admin.router,
                   dependencies=[Depends(requires_tier(Module.REPUTATION, Tier.VIEW))])


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "GW Migration System"}
