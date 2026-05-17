"""Check that every API path the frontend calls actually exists on the backend.

Lighter-weight than full OpenAPI codegen: parses the running backend's
/openapi.json, greps every `api.get/post/patch/delete/put(...)` in the
frontend, normalizes template literals, and reports any URL that doesn't
match a registered route.

Usage:
  ./venv/bin/python scripts/check_api_contracts.py
  ./venv/bin/python scripts/check_api_contracts.py --backend http://localhost:8000
  ./venv/bin/python scripts/check_api_contracts.py --frontend /path/to/frontend/src

The script DOESN'T try to typecheck request bodies — that would require
a full code-gen toolchain. It only finds dangling URL references.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict


FRONTEND_DEFAULT = "/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src"
BACKEND_DEFAULT = "http://localhost:8000"

# Match api.<verb>(...) — captures the literal URL or template literal body
API_CALL_RE = re.compile(
    r"\bapi\.(get|post|patch|delete|put)\(\s*[`'\"]([^`'\"]+)[`'\"]",
    re.MULTILINE,
)


def fetch_openapi(base: str) -> dict:
    url = base.rstrip("/") + "/openapi.json"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def index_routes(spec: dict) -> dict:
    """Return {(METHOD, path_template): operation_id-ish}.
    Strips the /api prefix if present so callers can find a match either way.
    """
    out = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in (ops or {}).items():
            if method.lower() not in ("get", "post", "patch", "delete", "put"):
                continue
            normalized = path
            if normalized.startswith("/api/"):
                normalized = normalized[4:]
            out[(method.upper(), normalized)] = op.get("operationId") or "?"
    return out


def normalize_call_url(raw: str) -> str:
    """Turn a JSX api-call URL into a path template that matches FastAPI's
    OpenAPI shape. Examples:
      `/pellets/visits/${visitId}/insert`  →  /pellets/visits/{p}/insert
      '/pellets/dashboard'                 →  /pellets/dashboard
      `/larc/checkouts/${id}/decide?force=1` → /larc/checkouts/{p}/decide
    """
    # Drop query string
    url = raw.split("?", 1)[0]
    # Replace any ${...} template expression with {p}
    url = re.sub(r"\$\{[^}]+\}", "{p}", url)
    # Strip a leading /api if present
    if url.startswith("/api/"):
        url = url[4:]
    return url


def routes_to_lookup(routes: dict) -> dict:
    """Build a normalized {(method, templated_path)} set where every
    {name} → {p} so we can match it against frontend's normalized calls."""
    lookup = {}
    for (method, path), op_id in routes.items():
        norm = re.sub(r"\{[^}]+\}", "{p}", path)
        lookup[(method, norm)] = (path, op_id)
    return lookup


def scan_frontend(src_root: str) -> list[tuple[str, int, str, str]]:
    """Yield (file, line, method, raw_url) for every api.<verb> call."""
    out = []
    for dirpath, _dirs, files in os.walk(src_root):
        if "node_modules" in dirpath:
            continue
        for f in files:
            if not (f.endswith(".jsx") or f.endswith(".js") or f.endswith(".ts") or f.endswith(".tsx")):
                continue
            full = os.path.join(dirpath, f)
            try:
                with open(full, encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            for m in API_CALL_RE.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                out.append((full, line, m.group(1).upper(), m.group(2)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=BACKEND_DEFAULT)
    ap.add_argument("--frontend", default=FRONTEND_DEFAULT)
    ap.add_argument("--quiet", action="store_true",
                    help="Only print the summary line + failures (no per-route stats)")
    args = ap.parse_args()

    try:
        spec = fetch_openapi(args.backend)
    except Exception as e:
        print(f"FAIL: couldn't fetch {args.backend}/openapi.json: {e}", file=sys.stderr)
        sys.exit(2)

    routes = index_routes(spec)
    lookup = routes_to_lookup(routes)
    calls = scan_frontend(args.frontend)

    # Build a per-method list of normalized route paths split into segments
    # so we can fuzzy-match: a literal frontend segment can fill a backend
    # template parameter (e.g. /milestones/{kind} ← /milestones/done).
    by_method_segs: dict = defaultdict(list)
    for (method, norm), original in lookup.items():
        by_method_segs[method].append((norm.split("/"), norm, original))

    def fuzzy_match(method: str, norm_url: str) -> bool:
        target = norm_url.split("/")
        for segs, _norm, _orig in by_method_segs.get(method, []):
            if len(segs) != len(target):
                continue
            ok = True
            for a, b in zip(segs, target):
                if a == b:
                    continue
                # Either side may be {p}; that's a wildcard slot
                if a == "{p}" or b == "{p}":
                    continue
                ok = False
                break
            if ok:
                return True
        return False

    ok, fail = 0, []
    counts_by_path: dict = defaultdict(int)
    for file, line, method, raw in calls:
        norm = normalize_call_url(raw)
        key = (method, norm)
        if key in lookup or fuzzy_match(method, norm):
            ok += 1
            counts_by_path[norm] += 1
        else:
            fail.append((file, line, method, raw, norm))

    rel = lambda p: p.replace(args.frontend.rstrip("/") + "/", "")
    if not args.quiet:
        print(f"Scanned {len(calls)} api.<verb> calls in {args.frontend}")
        print(f"Backend has {len(routes)} registered routes at {args.backend}")
        print()

    if fail:
        print(f"❌ {len(fail)} dangling call(s):")
        for file, line, method, raw, norm in fail:
            print(f"  {rel(file)}:{line}  {method} {raw}")
            print(f"    normalized → {norm}  (no matching route)")
    else:
        print(f"✓ All {ok} frontend api calls resolve to a backend route.")

    if not args.quiet and len(counts_by_path) <= 50:
        print()
        print("Top hit paths:")
        for p, n in sorted(counts_by_path.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {n:4d}  {p}")

    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
