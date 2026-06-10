import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Returns the currently authenticated user plus convenience flags.
 *
 * Flags are computed server-side from the per-module tier model
 * (see backend app/permissions/catalog.py):
 *   - isAdmin    → user is a Super Admin
 *   - isBilling  → Active AR tier >= View
 *   - isClinical → Chart View AND not billing/admin
 *
 * `tier(module, minTier?)` reads the resolved tier on a specific module.
 * Pass `minTier` to get a boolean ("has at least this much"). Without
 * `minTier`, returns the numeric tier (0–50). Super-admins always pass
 * the boolean form — matches backend `requires_tier` behaviour.
 *
 * Use MODULE.X and TIER.X from ../routes.jsx for the arguments. Example:
 *   import { MODULE, TIER } from '../routes.jsx'
 *   const canEdit = tier(MODULE.PELLETS, TIER.MANAGE)
 */
export function useCurrentUser() {
  const q = useQuery({
    queryKey: ['current-user'],
    queryFn: () => api.get('/auth/me').then(r => r.data),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const data = q.data || {}
  const moduleTiers = data.module_tiers || {}
  const isSuperAdmin = !!data.is_super_admin

  const tier = (mod, minTier) => {
    if (minTier == null) return moduleTiers[mod] ?? 0
    // Super-admin satisfies every boolean tier check — same rule
    // requires_tier uses on the backend.
    if (isSuperAdmin) return true
    return (moduleTiers[mod] ?? 0) >= minTier
  }

  return {
    email: data.email,
    name: data.name,
    picture: data.picture,
    moduleTiers,
    tier,
    isAdmin:    !!data.is_admin,
    isBilling:  !!data.is_billing,
    isClinical: !!data.is_clinical,
    isSuperAdmin,
    canSeeBilling: !!data.is_admin || !!data.is_billing,
    isLoading: q.isLoading,
  }
}
