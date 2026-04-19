import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

// Returns FaxLog rows for a chart; auto-refreshes every 30s while any row is non-terminal.
export function useFaxByChart(chartNumber, { enabled = true } = {}) {
  return useQuery({
    queryKey: ['fax-by-chart', chartNumber],
    queryFn: () => api.get(`/fax/by-chart/${chartNumber}`).then(r => r.data),
    enabled: !!chartNumber && enabled,
    refetchInterval: (query) => {
      const data = query.state?.data
      return Array.isArray(data) && data.some(r => r.status === 'queued' || r.status === 'sent')
        ? 30_000
        : false
    },
  })
}

// Given an array of fax log rows, return a map of doc_id → most-recent row.
export function faxByDocId(rows) {
  const out = {}
  if (!Array.isArray(rows)) return out
  // rows arrive newest-first from the API; iterate that way so the first hit per
  // doc_id wins and later (older) rows don't overwrite it.
  for (const r of rows) {
    for (const docId of r.doc_ids || []) {
      if (!out[docId]) out[docId] = r
    }
  }
  return out
}
