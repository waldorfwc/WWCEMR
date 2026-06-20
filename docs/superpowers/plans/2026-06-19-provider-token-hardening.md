# Provider-Token Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use `- [ ]` checkboxes. Confirm the three open decisions (below) before implementing — they change schema/behavior.

**Goal:** Harden the Missing Charges *provider self-service* tokens (the no-login email links into `/p/missing-charges/{token}`) against the two residual risks flagged in the security audit: (1) the signing key is shared with the app's main `SECRET_KEY` (no key separation), and (2) tokens are 60-day stateless bearer credentials with **no revocation** — a leaked/forwarded link grants PHI access (a provider's panel: patient names, MRNs) for 60 days with no way to kill it.

**Background / current state (verified):**
- `backend/app/services/missing_charges_token.py`: `_secret()` returns `os.environ.get("MISSING_CHARGES_TOKEN_SECRET") or settings.secret_key`. `MISSING_CHARGES_TOKEN_SECRET` is **not** set in prod, so it currently signs with `settings.secret_key` (`SECRET_KEY`). `mint_token(provider, *, ttl_days=60)` → HS256 JWT, claims `{provider, iss="wwc-billing", kind="missing_charges_provider", iat, exp}`. `decode_token(token)` validates signature/issuer/exp/kind.
- Token consumers (public, no login): `GET /api/billing/missing-charges/provider/{token}`, `POST /api/billing/missing-charges/provider/{token}/{charge_id}` (`app/routers/missing_charges.py`). Lateral-access check (`c.primary_provider == provider`) already present.
- Token minters: `POST /billing/missing-charges/provider-tokens` (`mint_provider_token`, gated `Tier.WORK`) and the weekly email — `app/services/missing_charges_email.py send_provider_emails` → `token_svc.mint_token(provider)`. The weekly email runs both via the in-process scheduler (`fax_poller`, uses the backend-service env) **and** as the Cloud Run Job `missing-charges-weekly` (`app/jobs/run.py`) — both need the new secret env.
- Existing revocation precedent to mirror: **`User.token_version`** — auth JWTs embed `tv`; `get_current_user` rejects on mismatch; deactivation bumps it (`admin_users.py`, and `google_sync` after recent work). Reuse this exact pattern for providers.
- `ProviderUserMapping` (`app/models/missing_charge.py`): `provider_name` (unique), `user_email`, `is_active`, `is_ignored`, `created_at`, `created_by`. Natural home for a per-provider `token_version`.

## Open decisions (confirm before building)
1. **TTL.** Keep 60 days, or cut to **14** (recommended — shorter exposure window; weekly emails reissue anyway so a 14-day link still covers a full cycle + slack). 
2. **Revocation key location.** Add `token_version` to `ProviderUserMapping` (recommended — providers already have a row there once mapped) vs. a new `MissingChargesProviderToken` table (needed only if you want to revoke providers that have no mapping). Recommendation: column on `ProviderUserMapping`, auto-creating a row on first mint if absent.
3. **Admin revoke UX now, or mechanism only?** Recommendation: ship the mechanism + a single `POST .../provider-tokens/{provider}/revoke` endpoint now; add a frontend button in a follow-up.

---

## Part A — Dedicated signing secret (key separation)

### A1. Provision the secret (you run these — needs your gcloud auth)
- [ ] Create + seed a strong dedicated secret:
```bash
gcloud secrets create missing-charges-token-secret --replication-policy=automatic --project=wwc-solutions
python3 -c "import secrets; print(secrets.token_urlsafe(48))" | tr -d '\n' \
  | gcloud secrets versions add missing-charges-token-secret --data-file=- --project=wwc-solutions
```
- [ ] Grant the running service accounts access (backend service + any job SA that mints — `worker` if jobs use it):
```bash
for sa in backend worker; do
  gcloud secrets add-iam-policy-binding missing-charges-token-secret \
    --member="serviceAccount:${sa}@wwc-solutions.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" --project=wwc-solutions
done
```
- [ ] Mount on the backend service:
```bash
gcloud run services update backend --region=us-east4 --project=wwc-solutions \
  --update-secrets="MISSING_CHARGES_TOKEN_SECRET=missing-charges-token-secret:latest"
```
- [ ] Mount on the weekly-email Cloud Run Job (it mints tokens):
```bash
gcloud run jobs update missing-charges-weekly --region=us-east4 --project=wwc-solutions \
  --update-secrets="MISSING_CHARGES_TOKEN_SECRET=missing-charges-token-secret:latest"
```

### A2. Code (no change needed — already reads the env first)
`_secret()` already prefers `MISSING_CHARGES_TOKEN_SECRET` over `settings.secret_key`, so once the env is mounted it uses the dedicated secret automatically. Optionally tighten: in prod (`K_SERVICE` set) refuse to fall back to `settings.secret_key` for this token kind — fail loud if `MISSING_CHARGES_TOKEN_SECRET` is unset, so we can't silently regress to the shared key.
- [ ] (optional) add the prod-only guard in `_secret()`; unit-test it.

### A3. Impact
Switching the secret **invalidates all outstanding provider links** (signed with the old `SECRET_KEY`). That's acceptable/desirable — reissue via **Missing Charges → Email Providers**. Communicate before flipping, or flip right before a weekly send.

---

## Part B — Revocation (per-provider token_version)

### B1. Schema
- [ ] Add `token_version = Column(Integer, nullable=False, default=0)` to `ProviderUserMapping` (`app/models/missing_charge.py`).
- [ ] Lightweight migration in `app/database.py _apply_lightweight_migrations()` `needed` list: `("provider_user_mappings", "token_version", "INTEGER DEFAULT 0")`.

### B2. Token carries + checks the version
- [ ] `mint_token(provider, *, ttl_days=...)`: look up the provider's `ProviderUserMapping` (create one with `token_version=0` if absent — minting implies an active link), embed `ptv = mapping.token_version` in the JWT claims. (Pass `db` into `mint_token`, or add `mint_token_for(db, provider)` so it can read the version — keep the pure `mint_token` for tests.)
- [ ] `decode_token` stays signature/exp/kind-only (pure, no DB). Add a separate `verify_provider_token(db, token) -> Optional[dict]` that calls `decode_token` then checks `payload["ptv"] == current token_version for payload["provider"]` (treat a missing mapping as version 0; reject if the token's `ptv` is below the stored version). The two public portal endpoints call `verify_provider_token` instead of `decode_token`.
- [ ] Back-compat: tokens minted before this change have no `ptv`. Decide: treat missing `ptv` as 0 (still valid until they expire) — acceptable since Part A already invalidates them on the secret flip. Document it.

### B3. Revoke action
- [ ] `POST /billing/missing-charges/provider-tokens/{provider}/revoke` (gated `Tier.WORK` or `MANAGE` — your call), bumps `mapping.token_version += 1` (creating the row if needed). Returns the new version.
- [ ] Auto-bump on offboarding: when a `ProviderUserMapping` is set `is_ignored=true`/`is_active=false` or **deleted** (`patch_provider_mapping` / `delete_provider_mapping`), bump `token_version` so any live link for a now-inactive provider dies. (Mirror how `User` deactivation bumps `token_version`.)

### B4. (optional) Frontend
- [ ] In the Email Providers panel mapping list, add a small "Revoke links" action per provider that hits the revoke endpoint (follow-up; not required for the mechanism).

---

## Part C — TTL reduction (optional, recommended)
- [ ] Change `TOKEN_TTL_DAYS` 60 → 14 (or your chosen value). Weekly reissue keeps providers covered; shorter window limits a leaked link's lifetime. Pairs well with revocation.

---

## Testing
- [ ] `_secret()` prefers `MISSING_CHARGES_TOKEN_SECRET` when set; (if A2 guard added) raises in prod when unset.
- [ ] mint→verify roundtrip embeds + checks `ptv`; a token whose `ptv` < stored version → rejected; bumping `token_version` invalidates a previously-valid token; a fresh mint after the bump works.
- [ ] revoke endpoint bumps the version (auth-gated); `is_ignored`/delete auto-bump kills outstanding links.
- [ ] portal endpoints (`/provider/{token}`, `/provider/{token}/{id}`) use `verify_provider_token` (401 on stale `ptv`); lateral-access check unchanged.
- [ ] TTL change: a token minted with the new TTL expires at the right time (freeze/inject time).

## Rollout
1. Land code (Parts A2 guard + B + C) behind the existing behavior (env not yet set → still signs with SECRET_KEY, `ptv` absent treated as 0 → no breakage on deploy).
2. Deploy backend + re-point the `missing-charges-weekly` job image.
3. Provision + mount `MISSING_CHARGES_TOKEN_SECRET` (Part A1) — this is the moment outstanding links die; do it right before a weekly send or announce it.
4. Verify: mint a token (admin endpoint), hit the portal (200), revoke, hit again (401).

## Conventions / guardrails
No secrets in source (Secret Manager only); `now_utc_naive()`; gcloud always `--project=wwc-solutions`; the weekly-email Cloud Run Job reuses the backend image — re-point it after a backend deploy. See memory: [[feedback_no_secrets_in_code]], [[feedback_cloudrun_cron_reliability]].
