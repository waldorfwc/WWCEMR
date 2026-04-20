import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Returns the currently authenticated user plus convenience flags.
 * {email, name, picture, group, isAdmin, isBilling, isClinical, canSeeBilling}
 *
 * While loading, every flag is false and `group` is undefined — callers should
 * gate clinical-hiding UI on `canSeeBilling` (false during load is the safer
 * default for clinical-like screens).
 */
export function useCurrentUser() {
  const q = useQuery({
    queryKey: ['current-user'],
    queryFn: () => api.get('/auth/me').then(r => r.data),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const data = q.data || {}
  const group = data.group
  return {
    email: data.email,
    name: data.name,
    picture: data.picture,
    group,
    isAdmin: group === 'admin',
    isBilling: group === 'billing',
    isClinical: group === 'clinical',
    canSeeBilling: group === 'admin' || group === 'billing',
    isLoading: q.isLoading,
  }
}
