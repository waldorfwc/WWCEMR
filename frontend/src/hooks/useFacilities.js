import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Returns the list of active facilities + a lookup helper.
 *   facilities → array of {code, label, address, sort_order}
 *   labelOf(code) → human label or the code if not found
 */
export function useFacilities() {
  const q = useQuery({
    queryKey: ['facilities-picklist'],
    queryFn: () => api.get('/surgery/picklists/facilities').then(r => r.data.facilities),
    staleTime: 60_000,
  })
  const facilities = q.data || []
  const map = Object.fromEntries(facilities.map(f => [f.code, f.label]))
  return {
    facilities,
    labelOf: (code) => map[code] || code,
    isLoading: q.isLoading,
  }
}
