import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Activity, CheckCircle2, FileUp, FlaskConical, FileSignature,
  Send, DollarSign, CalendarCheck, Circle,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { MODULE, TIER } from '../routes.jsx'
import LoadingState from '../components/LoadingState'

// Human label + icon/tone for each activity kind. Falls back to a neutral dot.
const KIND_META = {
  mammo_uploaded:     { label: 'Mammogram uploaded', icon: FileUp,        tone: 'text-plum-600' },
  labs_self_reported: { label: 'Labs self-reported', icon: FlaskConical,  tone: 'text-plum-600' },
  consent_signed:     { label: 'Consent signed',     icon: FileSignature, tone: 'text-emerald-600' },
  consent_sent:       { label: 'Consent sent',       icon: Send,          tone: 'text-plum-600' },
  payment_made:       { label: 'Payment made',       icon: DollarSign,    tone: 'text-emerald-600' },
  booked:             { label: 'Booked',             icon: CalendarCheck, tone: 'text-emerald-600' },
}

const VERIFIABLE = new Set(['mammo_uploaded', 'labs_self_reported'])


export default function PelletActivity() {
  const qc = useQueryClient()
  const { tier } = useCurrentUser()
  const canVerify = tier(MODULE.PELLETS, TIER.WORK)

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-activity'],
    queryFn: () => api.get('/pellets/activity').then(r => r.data),
    refetchInterval: 60_000,
  })

  const verify = useMutation({
    mutationFn: (id) => api.post(`/pellets/activity/${id}/verify`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-activity'] })
      qc.invalidateQueries({ queryKey: ['pellet-activity-unread'] })
    },
  })

  const markAllRead = useMutation({
    mutationFn: () => api.post('/pellets/activity/read-all').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-activity'] })
      qc.invalidateQueries({ queryKey: ['pellet-activity-unread'] })
    },
  })

  const feed = data?.items || []

  return (
    <div>
      <div className="mb-4">
        <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Patient Activity</h1>
        <p className="text-muted text-[12px] mt-0.5">
          A live feed of patient actions. Verify mammogram and lab uploads to clear them.
        </p>
      </div>

      <section className="card !p-0 overflow-hidden">
        <header className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
          <div className="flex items-center gap-2">
            <Activity size={16} className="text-plum-600" />
            <h2 className="font-serif font-semibold text-ink text-[16px] m-0">Patient Activity</h2>
          </div>
          <button
            className="text-[11px] px-2 py-1 rounded border border-border-subtle hover:bg-plum-50 disabled:opacity-50"
            onClick={() => markAllRead.mutate()}
            disabled={markAllRead.isPending || feed.every(r => r.read_at)}
          >
            Mark All Read
          </button>
        </header>

        {isLoading ? (
          <LoadingState />
        ) : feed.length === 0 ? (
          <div className="flex items-center justify-center text-sm text-muted py-10">
            No patient activity yet.
          </div>
        ) : (
          <ul className="divide-y divide-border-subtle max-h-[70vh] overflow-y-auto">
            {feed.map(row => {
              const meta = KIND_META[row.kind]
              const Icon = meta?.icon || Circle
              const unread = !row.read_at
              const verifiable = VERIFIABLE.has(row.kind)
              return (
                <li
                  key={row.id}
                  className={`px-4 py-3 flex items-start gap-3 ${
                    unread ? 'border-l-2 border-l-amber-500 bg-amber-50/40' : ''
                  }`}
                >
                  <Icon size={16} className={`shrink-0 mt-0.5 ${meta?.tone || 'text-gray-400'}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[13px] font-medium text-gray-800">
                        {meta?.label || row.kind}
                      </span>
                      <span className="shrink-0 inline-flex items-center gap-1.5">
                        {unread && <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />}
                        <span className="text-[10px] text-muted whitespace-nowrap">
                          {fmt.dateTime(row.created_at)}
                        </span>
                      </span>
                    </div>
                    <div className="text-[11px] text-gray-500 mt-0.5">
                      {row.patient_name}
                      {row.chart_number && <> · <span className="font-mono">{row.chart_number}</span></>}
                    </div>
                    {row.summary && (
                      <div className="text-[12px] text-gray-700 mt-1">{row.summary}</div>
                    )}
                    {verifiable && (
                      <div className="mt-2">
                        {row.handled_at ? (
                          <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700 font-medium">
                            <CheckCircle2 size={13} /> Verified
                          </span>
                        ) : canVerify ? (
                          <button
                            className="text-[11px] px-2 py-1 rounded border border-emerald-300 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                            onClick={() => verify.mutate(row.id)}
                            disabled={verify.isPending}
                          >
                            Verify
                          </button>
                        ) : null}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
