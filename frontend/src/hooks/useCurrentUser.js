import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Returns the currently authenticated user plus convenience flags.
 *
 * Flags are computed server-side from the user's group memberships and
 * effective permissions (Phase 5 RBAC). Names kept the same for caller
 * stability:
 *   - isAdmin    → has user:manage permission (in Admin group, typically)
 *   - isBilling  → has claim:read permission (any billing-flavored group)
 *   - isClinical → has chart:read or chart:edit permission
 *
 * `effectivePermissions` is the full permission set as a string array —
 * use directly for fine-grained UI gating (e.g. show a button only if
 * user has `payment:post`).
 */
export function useCurrentUser() {
  const q = useQuery({
    queryKey: ['current-user'],
    queryFn: () => api.get('/auth/me').then(r => r.data),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const data = q.data || {}
  const perms = data.effective_permissions || []
  const has = (p) => perms.includes(p)
  return {
    email: data.email,
    name: data.name,
    picture: data.picture,
    effectivePermissions: perms,
    has,
    isAdmin:    !!data.is_admin,
    isBilling:  !!data.is_billing,
    isClinical: !!data.is_clinical,
    canSeeBilling: !!data.is_admin || !!data.is_billing,
    isLoading: q.isLoading,
  }
}
