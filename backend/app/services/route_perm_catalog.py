"""Routes-vs-permissions catalog — startup audit.

Walks `app.routes`, identifies endpoints that have NO permission guard at
either the router-include level or the route level, and prints a punch
list. Logged once at startup; CI can later promote this to a failing
check.

Permission detection scans for dependencies whose source contains the
substring `require_permission` — covers both:
  • `app.include_router(..., dependencies=[Depends(require_permission(...))])`
  • `def endpoint(..., current_user: dict = Depends(require_permission(...)))`

Public endpoints (login, health, OpenAPI, docs, OAuth callbacks) are
allowlisted explicitly.
"""
from __future__ import annotations

import inspect
import logging
from typing import Iterable

from fastapi import FastAPI
from fastapi.routing import APIRoute


_LOG = logging.getLogger(__name__)


# Routes that legitimately have no permission gate — login, public docs,
# OAuth callbacks, health checks, signed-token portals (token IS the gate).
_PUBLIC_ALLOWLIST = {
    "/api/auth/login",
    "/api/auth/google",
    "/api/auth/callback",
    "/api/auth/config",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/whoami",            # returns current user only
    "/api/auth/me",
    "/api/health",
    "/api/version",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/openapi.json",
    "/favicon.ico",
    # Signed-token portals — the token in the URL IS the auth check
    "/api/missing-charges/portal/{token}",
    "/api/missing-charges/portal/{token}/lines",
    "/api/missing-charges/portal/{token}/submit",
    "/api/billing/missing-charges/provider/{token}",
    "/api/billing/missing-charges/provider/{token}/{charge_id}",
    # DocuSign + Calendly webhooks — HMAC-signed payloads
    "/api/docusign/webhook",
    "/api/calendly/webhook",
}

# Prefix allowlist — anything under these prefixes is presumed token-gated
# at the route level via a custom dependency (e.g. require_patient_token).
_PUBLIC_PREFIX_ALLOWLIST = (
    "/api/p/",              # patient-facing portal (require_patient_token)
    "/api/patient/portal/", # patient self-service portal (require_portal_token)
)


def _looks_like_permission_guard(dep) -> bool:
    """Return True when a FastAPI dependency's callable references
    `require_permission` (either directly, or as a closure)."""
    fn = getattr(dep, "dependency", None) or dep
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        # builtin / lambda — can't introspect; assume not a guard
        return False
    return "require_permission" in src or "get_current_user" in src


def _route_has_guard(route: APIRoute) -> bool:
    """Endpoint-level: scan the route's dependant tree."""
    deps = list(getattr(route, "dependencies", []) or [])
    # endpoint signature dependencies
    if route.dependant:
        deps += [d.cache_key[0] if hasattr(d, "cache_key") else d
                  for d in (route.dependant.dependencies or [])]
        # plus the raw Depends() instances on parameters
        for sub in route.dependant.dependencies:
            if getattr(sub, "call", None):
                if _looks_like_permission_guard(sub.call):
                    return True
    for d in deps:
        if _looks_like_permission_guard(d):
            return True
    return False


def audit_routes(app: FastAPI) -> dict:
    """Return {ok, unguarded: [(method, path)], total}. Logs a punch list."""
    unguarded: list[tuple[str, str]] = []
    total = 0
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for method in (r.methods or []):
            if method == "HEAD":
                continue
            total += 1
            if r.path in _PUBLIC_ALLOWLIST:
                continue
            if any(r.path.startswith(p) for p in _PUBLIC_PREFIX_ALLOWLIST):
                continue
            if _route_has_guard(r):
                continue
            unguarded.append((method, r.path))
    if unguarded:
        _LOG.warning(
            "route_perm_catalog: %d unguarded endpoint(s) (no require_permission "
            "and not in the public allowlist):", len(unguarded))
        for method, path in unguarded[:40]:
            _LOG.warning("  %-6s %s", method, path)
        if len(unguarded) > 40:
            _LOG.warning("  ... and %d more", len(unguarded) - 40)
    else:
        _LOG.info("route_perm_catalog: all %d endpoints have a permission "
                  "guard or are in the public allowlist", total)
    return {"total": total, "unguarded": unguarded}
