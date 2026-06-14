# Load .env into os.environ before any module reads it. Pydantic Settings
# parses .env into its own object but doesn't push values back to the env;
# integrations like ringcentral_client read os.environ directly.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

import os

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.services.request_context import (
    RequestIdMiddleware, install_request_id_logging,
)

from app.database import init_db
from app import soft_delete as _soft_delete  # noqa: F401  (registers global Surgery soft-delete filter)
from app.routers import imports, claims, patients, denials, appeals, eob, audit
from app.routers import ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, admin_groups, service_lines, claim_adjustments, service_line_adjustments, charge_imports, claim_id_bootstrap, era_posting, adjustment_codes, transaction_detail_imports, active_ar, active_ar_filter_presets, bank_recon, checklist, recalls, recall_filter_presets, training, surgery, surgery_config, patient_surgery, patient_portal, boldsign, consent_templates, surgery_filter_presets, larc, pellet, billing_documents, missing_charges, personal_tasks, code_helper, insurance_contacts, admin_cleanup
from app.routers import google_sync as google_sync_router
from app.routers import admin_tiers, admin_practice_settings
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

# Per-module tier gates live in app/permissions/dependencies.py. Each
# include_router below uses `requires_tier(Module.X, Tier.Y)` directly.
# The legacy verb-based catalog (BILLING_READ / AUDIT_READ / etc.) was
# removed in Phase 4 of the permissions redesign.
from app.permissions.dependencies import requires_super_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_request_id_logging()
    init_db()
    from app.services.fax_poller import start_scheduler
    sched = start_scheduler()
    # Route-permission audit. Returns {total, unguarded}. We promote this
    # from a WARNING to a hard startup failure on Cloud Run (K_SERVICE is
    # injected by the platform) so an ungated router can't ship. Local
    # development still gets only the WARNING — devs iterating on a new
    # router shouldn't be blocked from running the app while they wire up
    # the gate. (Fable design review note 2.)
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from app.services.route_perm_catalog import audit_routes
        report = audit_routes(app)
        if report["unguarded"] and os.environ.get("K_SERVICE"):
            preview = ", ".join(f"{m} {p}" for m, p in report["unguarded"][:10])
            more = f" (+{len(report['unguarded']) - 10} more)" if len(report["unguarded"]) > 10 else ""
            raise RuntimeError(
                f"Refusing to start: {len(report['unguarded'])} ungated "
                f"endpoint(s) detected by route_perm_catalog: {preview}{more}. "
                "Add a require_permission/requires_tier dependency or "
                "allowlist the path in route_perm_catalog._PUBLIC_ALLOWLIST."
            )
    except RuntimeError:
        # Startup gate — propagate so Cloud Run keeps the previous revision.
        raise
    except Exception:
        _log.exception("route_perm_catalog failed")

    # Lint against reintroducing datetime.utcnow(). utils/dt.py is the
    # canonical source of "now" — utcnow() is deprecated in 3.12 and
    # produces naive datetimes that don't compare with tz-aware ones
    # (e.g. storage.blob_metadata). Same Cloud Run-only enforcement
    # pattern as the route audit. (Fable design review note 5.)
    try:
        from app.services.dt_lint import find_utcnow_hits
        utcnow_hits = find_utcnow_hits()
        if utcnow_hits and os.environ.get("K_SERVICE"):
            preview = ", ".join(f"{f}:{ln}" for f, ln in utcnow_hits[:10])
            more = f" (+{len(utcnow_hits) - 10} more)" if len(utcnow_hits) > 10 else ""
            raise RuntimeError(
                f"Refusing to start: {len(utcnow_hits)} datetime.utcnow "
                f"reference(s) found in app/. Use now_utc_naive() from "
                f"app/utils/dt.py instead — utcnow is deprecated and "
                f"produces tz-naive datetimes. Hits: {preview}{more}."
            )
    except RuntimeError:
        raise
    except Exception:
        _log.exception("dt_lint failed")

    try:
        yield
    finally:
        sched.shutdown(wait=False)


app = FastAPI(
    title="WWC App",
    description=(
        "Internal practice system for Waldorf Women's Care — patient "
        "charts, claims & AR, surgery scheduling, device tracking, "
        "pellets, bank reconciliation, and the office's day-to-day "
        "workflow."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Request-ID middleware MUST be added first (executes last on the
# request-going-in / first on the response-going-out — Starlette walks
# its middleware stack in reverse). That guarantees CORS gets to see
# the request_id when it logs, and the response carries X-Request-ID.
app.add_middleware(RequestIdMiddleware)

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
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# Unified error envelope: every error response carries {detail, code}
# so the frontend can switch on `code` instead of sniffing for the
# right shape. (Fable design review note 9.)
#   - detail: the human-readable message (or the validation error list)
#   - code:   short machine slug. Examples: "stale_data",
#             "validation_error", "internal_error", "unauthorized".
# HTTP status code is what it always was; nothing breaks for existing
# clients that only read `detail`.


def _http_code_default(status_code: int) -> str:
    """Default `code` slug when an HTTPException doesn't supply one."""
    if status_code == 401: return "unauthorized"
    if status_code == 403: return "forbidden"
    if status_code == 404: return "not_found"
    if status_code == 409: return "conflict"
    if status_code == 422: return "unprocessable"
    if status_code == 429: return "rate_limited"
    if 500 <= status_code < 600: return "server_error"
    return "http_error"


@app.exception_handler(StaleDataError)
async def _stale_data_handler(request: Request, exc: StaleDataError):
    return JSONResponse(
        status_code=409,
        content={
            "detail": "This record was modified by another user since you "
                      "opened it. Refresh and try again.",
            "code": "stale_data",
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # exc.errors() can embed a raw ValueError in each entry's "ctx" when a
    # custom field_validator raises ValueError; that object is not directly
    # JSON-serializable, so encode through jsonable_encoder (which stringifies
    # it) instead of handing it straight to JSONResponse and 500-ing.
    from fastapi.encoders import jsonable_encoder
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "code": "validation_error"},
    )


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Preserve the caller's detail (str or dict). If the detail is a
    # dict with a "code" key, surface that as the envelope code so
    # callers can attach machine-readable slugs by passing
    # `raise HTTPException(409, detail={"code": "duplicate", ...})`.
    detail = exc.detail
    code = None
    if isinstance(detail, dict):
        code = detail.get("code")
    if not code:
        code = _http_code_default(exc.status_code)
    headers = getattr(exc, "headers", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail, "code": code},
        headers=headers,
    )


def api_error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    """Helper for routes that want to attach a machine-readable `code`
    to an error response. The exception handler surfaces `code` in the
    response envelope alongside the human-readable detail.

    Example:
        raise api_error(409, "duplicate",
                         "A document with identical contents already exists.",
                         existing_id=str(existing.id))
    """
    detail: dict = {"code": code, "message": message}
    detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)

app.include_router(imports.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(claims.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(service_lines.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(claim_adjustments.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(service_line_adjustments.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(charge_imports.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(claim_id_bootstrap.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(era_posting.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(patients.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(denials.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(appeals.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(eob.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(audit.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.AUDIT_LOG, Tier.VIEW))])
app.include_router(ar.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(documents.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(intake.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(chart.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(fax.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(auth.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.MANAGE))])
# Fax routers gate on CHART (not ACTIVE_AR) — faxing is a chart-context
# workflow that both clinical and billing staff use to send PHI off the
# chart. Per-endpoint elevations live in the routers themselves and use
# CHART:WORK for write actions. (Fable design review note 2.)
app.include_router(fax_batch.router, prefix="/api", dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(fax_batch.log_router, prefix="/api", dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(admin_users.router, prefix="/api", dependencies=[Depends(requires_super_admin())])
app.include_router(admin_cleanup.router, prefix="/api", dependencies=[Depends(requires_super_admin())])
app.include_router(admin_groups.router, prefix="/api", dependencies=[Depends(requires_super_admin())])
# admin_tiers does its own per-route auth (Super Admin / per-module Admin),
# so it intentionally has no router-level dependency.
app.include_router(admin_tiers.router, prefix="/api")
# admin_practice_settings does its own Super-Admin check inside each endpoint.
app.include_router(admin_practice_settings.router, prefix="/api")
app.include_router(adjustment_codes.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(transaction_detail_imports.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(active_ar.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
app.include_router(active_ar_filter_presets.router, prefix="/api", dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))])
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
# BoldSign Connect webhook — no auth (BoldSign POSTs from outside;
# verified via HMAC).
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
app.include_router(google_sync_router.router, prefix="/api", dependencies=[Depends(requires_super_admin())])
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
    return {"status": "ok", "service": "WWC App"}
