import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Shield } from 'lucide-react'
import api from '../../utils/api'

const TIERS = [
  { value: 'view',   label: 'View'   },
  { value: 'work',   label: 'Work'   },
  { value: 'manage', label: 'Manage' },
  { value: 'admin',  label: 'Admin'  },
  { value: 'denied', label: 'Denied' },
]

export default function UserTierGrid() {
  const { email } = useParams()
  const decodedEmail = decodeURIComponent(email)
  const qc = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ['user-tiers', decodedEmail],
    queryFn: () =>
      api.get(`/admin/users/${encodeURIComponent(decodedEmail)}/tiers`)
         .then(r => r.data),
  })

  const set = useMutation({
    mutationFn: ({ module, tier }) =>
      api.put(
        `/admin/users/${encodeURIComponent(decodedEmail)}/overrides/${module}`,
        { tier },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-tiers', decodedEmail] }),
  })

  if (isLoading) return <div className="p-4 text-muted">Loading…</div>
  if (error) {
    return (
      <div className="p-4 text-sm text-red-700">
        Couldn't load tiers: {error.response?.data?.detail || error.message}
      </div>
    )
  }

  const onClick = (entry, choice) => {
    // Clicking the currently-active marker on an override clears it
    // (falls back to whatever the group grants).
    if (entry.tier === choice && entry.source_kind === 'override') {
      set.mutate({ module: entry.module, tier: null })
    } else {
      set.mutate({ module: entry.module, tier: choice })
    }
  }

  return (
    <div>
      <Link to="/admin" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
        <ArrowLeft size={12} /> Back to Admin
      </Link>
      <h1 className="font-serif font-semibold text-ink text-[22px] m-0">
        Permissions — {decodedEmail}
      </h1>
      <p className="text-muted text-[12px] mt-0.5 mb-3">
        Click a tier to set a per-user override. Click an active override
        marker to clear it (falls back to the group default).
      </p>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 border-b border-stone-200 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Module</th>
              {TIERS.map(t => (
                <th key={t.value}
                    className="px-2 py-2 font-medium text-center"
                    aria-label={t.label}>
                  {t.label}
                </th>
              ))}
              <th className="px-3 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {data?.tiers?.map(entry => (
              <tr key={entry.module} className="border-t border-stone-100 hover:bg-stone-50">
                <td className="px-3 py-2">{entry.label}</td>
                {TIERS.map(t => {
                  const active = entry.tier === t.value
                  return (
                    <td key={t.value} className="px-2 py-2 text-center">
                      <button
                        type="button"
                        onClick={() => onClick(entry, t.value)}
                        disabled={set.isPending}
                        aria-label={`Set ${entry.label} to ${t.label}`}
                        className={
                          active
                            ? 'inline-block w-3 h-3 rounded-full bg-plum-700'
                            : 'inline-block w-3 h-3 rounded-full border border-stone-300 hover:bg-plum-100'
                        }
                      />
                    </td>
                  )
                })}
                <td className="px-3 py-2 text-xs text-muted">
                  {entry.source_kind === 'super_admin' && (
                    <span className="inline-flex items-center gap-1 text-plum-700">
                      <Shield size={11} /> Super Admin
                    </span>
                  )}
                  {entry.source_kind === 'override' && 'Override'}
                  {entry.source_kind === 'group'    && entry.source_label}
                  {entry.source_kind === 'none'     && '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
