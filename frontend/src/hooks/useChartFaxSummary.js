import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

// Returns { [chart_number]: { fax_count, last_sent_at } } for every chart
// that has any FaxLog activity. Stale-time 2 minutes — plenty for a
// migration workflow where ops sees updates on next hover/nav.
export function useChartFaxSummary() {
  return useQuery({
    queryKey: ['fax-chart-summary'],
    queryFn: async () => {
      const rows = await api.get('/fax/chart-summary').then(r => r.data)
      const map = {}
      for (const r of rows) {
        map[r.chart_number] = { fax_count: r.fax_count, last_sent_at: r.last_sent_at }
      }
      return map
    },
    staleTime: 2 * 60 * 1000,
  })
}
