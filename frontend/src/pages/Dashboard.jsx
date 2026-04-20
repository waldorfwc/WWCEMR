import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import api, { fmt } from '../utils/api'

function Stat({ label, value, sub, subColor = 'text-muted', accent }) {
  return (
    <div
      className="stat"
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
    >
      <div className="eyebrow">{label}</div>
      <div className="display-number text-[26px] leading-none mt-1">{value}</div>
      {sub && <div className={`text-[11px] mt-1 ${subColor}`}>{sub}</div>}
    </div>
  )
}

function greeting(hour) {
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: () => api.get('/dashboard/summary').then(r => r.data),
  })

  const { data: faxes } = useQuery({
    queryKey: ['fax-recent'],
    queryFn: () => api.get('/fax/recent?limit=5')
      .then(r => Array.isArray(r.data) ? r.data : [])
      .catch(() => []),  // 404 / error → empty list, card renders empty state
  })

  const now = new Date()
  const delta = data && data.collected_prior_30d > 0
    ? Math.round(((data.collected_30d - data.collected_prior_30d) / data.collected_prior_30d) * 100)
    : null

  return (
    <div>
      {/* Header row */}
      <div className="flex items-baseline justify-between mb-5">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[26px] tracking-tight m-0">
            {greeting(now.getHours())}
          </h1>
          <div className="text-muted text-[13px] mt-0.5">
            {format(now, 'EEEE, MMMM d')} · snapshot as of {format(now, 'h:mm a')}
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" disabled title="Window selector — Phase 2">
            Last 30 days ▾
          </button>
          <a href="/claims" className="btn-primary">+ New claim</a>
        </div>
      </div>

      {/* Hero KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <Stat
          label="Collected · 30d"
          value={data ? fmt.currency(data.collected_30d) : '—'}
          sub={delta !== null ? `${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta)}% vs prior 30` : 'no prior data'}
          subColor={delta !== null && delta >= 0 ? 'text-success' : 'text-muted'}
        />
        <Stat
          label="Outstanding"
          value={data ? fmt.currency(data.outstanding_total) : '—'}
          sub={data ? `across ${data.outstanding_count.toLocaleString()} charges` : ''}
        />
        <Stat
          label="Open claims"
          value={data ? data.open_claims.toLocaleString() : '—'}
          sub={data ? `${data.claims_submitted_7d} submitted this week` : ''}
        />
        <Stat
          label="Timely filing · ≤7d"
          value={data ? data.timely_filing_at_risk_7d.toLocaleString() : '—'}
          sub={data && data.timely_filing_at_risk_7d > 0 ? 'needs submission' : 'clear'}
          subColor={data && data.timely_filing_at_risk_7d > 0 ? 'text-danger' : 'text-success'}
          accent={data && data.timely_filing_at_risk_7d > 0 ? '#C62828' : undefined}
        />
      </div>

      {/* Resolved by window + denials */}
      <div className="grid grid-cols-3 gap-3 mb-3">
        <div className="card col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-serif font-semibold text-ink text-[15px] m-0">Claims resolved</h2>
            <div className="text-[11px] text-muted">by window</div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {(['30d', '60d', '90d']).map(k => (
              <div key={k}>
                <div className="eyebrow">Last {k}</div>
                <div className="display-number text-[20px] mt-1">
                  {data ? data.resolved[k].count.toLocaleString() : '—'}
                </div>
                <div className="text-[11px] text-muted">
                  {data ? `${fmt.currency(data.resolved[k].collected)} collected` : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
        <Stat
          label="Denied claims"
          value={data ? data.denied_open.toLocaleString() : '—'}
          sub={data && data.denied_delta_7d > 0 ? `▲ ${data.denied_delta_7d} since last week` : 'no new denials'}
          subColor={data && data.denied_delta_7d > 0 ? 'text-danger' : 'text-muted'}
        />
      </div>

      {/* Recent faxes + attention */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card">
          <h2 className="font-serif font-semibold text-ink text-[15px] m-0 mb-2">
            Recent faxes
          </h2>
          {faxes && faxes.length > 0 ? (
            <div>
              {faxes.map(f => (
                <div
                  key={f.id}
                  className="text-[12px] text-ink flex justify-between py-1.5 border-b border-plum-100 last:border-b-0"
                >
                  <span>{f.patient_name || f.chart_number}</span>
                  <span className={f.status === 'failed' ? 'text-warning' : 'text-success'}>
                    {f.status === 'sent' ? `✓ ${f.sent_at ? format(new Date(f.sent_at), 'h:mm a') : ''}` : f.status}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[12px] text-muted py-6 text-center">No recent faxes yet.</div>
          )}
        </div>

        <div className="card">
          <h2 className="font-serif font-semibold text-ink text-[15px] m-0 mb-2">
            Needs your attention
          </h2>
          {data ? (
            <div className="text-[13px] text-ink">
              <div className="py-1.5 border-b border-plum-100 flex justify-between">
                <span>Claims approaching timely filing</span>
                <span className="font-medium">{data.attention.timely_filing}</span>
              </div>
              <div className="py-1.5 border-b border-plum-100 flex justify-between">
                <span>ERAs waiting to be posted</span>
                <span className="font-medium">{data.attention.eras_unposted}</span>
              </div>
              <div className="py-1.5 flex justify-between">
                <span>Fax failures to retry</span>
                <span className="font-medium">{data.attention.fax_failures}</span>
              </div>
            </div>
          ) : (
            <div className="text-[12px] text-muted py-6 text-center">
              {isLoading ? 'Loading...' : 'Dashboard unavailable.'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
