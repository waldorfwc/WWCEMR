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
 * `has(legacyPerm)` is kept for backwards compatibility with call-sites
 * that haven't migrated to the tier model yet. It maps a handful of
 * legacy verb strings to the equivalent (module, minTier) check.
 *
 * `tier(module, minTier?)` reads the resolved tier on a specific module.
 * Pass minTier to get a boolean ("has at least this much"). Without
 * minTier, returns the numeric tier (0–50).
 */

const VIEW = 10, WORK = 20, MANAGE = 30, ADMIN = 40

// Legacy verb → (module, minTier). Add entries here as more call-sites
// surface during cleanup. Anything not in this table is treated as
// "Super Admin only" so legacy gates fail closed rather than open.
const LEGACY_PERM_TO_TIER = {
  'recall:work':        ['recall', WORK],
  'surgery:read':       ['surgery', VIEW],
  'surgery:work':       ['surgery', WORK],
  'larc:read':          ['device_larc', VIEW],
  'larc:checkout':      ['device_larc', WORK],
  'pellet:read':        ['pellets', VIEW],
  'checklist:manage':   ['my_checklist', MANAGE],
}

export function useCurrentUser() {
  const q = useQuery({
    queryKey: ['current-user'],
    queryFn: () => api.get('/auth/me').then(r => r.data),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const data = q.data || {}
  const moduleTiers = data.module_tiers || {}

  const tier = (mod, minTier) => {
    const t = moduleTiers[mod] ?? 0
    return minTier == null ? t : t >= minTier
  }

  const has = (legacyPerm) => {
    if (data.is_super_admin) return true
    const map = LEGACY_PERM_TO_TIER[legacyPerm]
    if (!map) return false
    return tier(map[0], map[1])
  }

  return {
    email: data.email,
    name: data.name,
    picture: data.picture,
    moduleTiers,
    tier,
    has,
    isAdmin:    !!data.is_admin,
    isBilling:  !!data.is_billing,
    isClinical: !!data.is_clinical,
    isSuperAdmin: !!data.is_super_admin,
    canSeeBilling: !!data.is_admin || !!data.is_billing,
    isLoading: q.isLoading,
  }
}
