import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import api from '../../utils/api'

const TIERS = [
  { value: 'view',   label: 'View'   },
  { value: 'work',   label: 'Work'   },
  { value: 'manage', label: 'Manage' },
  { value: 'admin',  label: 'Admin'  },
]

export default function GroupTierGrid() {
  const { groupId } = useParams()
  const qc = useQueryClient()

  const { data: group } = useQuery({
    queryKey: ['admin-group', groupId],
    queryFn: () => api.get(`/admin/groups/${groupId}`).then(r => r.data),
  })

  const { data, isLoading, error } = useQuery({
    queryKey: ['group-tiers', groupId],
    queryFn: () => api.get(`/admin/groups/${groupId}/tiers`).then(r => r.data),
  })

  const set = useMutation({
    mutationFn: ({ module, tier }) =>
      api.put(`/admin/groups/${groupId}/tiers/${module}`, { tier }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['group-tiers', groupId] }),
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
    // Clicking the currently-active marker clears the grant for this module.
    if (entry.tier === choice) {
      set.mutate({ module: entry.module, tier: null })
    } else {
      set.mutate({ module: entry.module, tier: choice })
    }
  }

  return (
    <div>
      <Link to="/admin/groups" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
        <ArrowLeft size={12} /> Back to Groups
      </Link>
      <h1 className="font-serif font-semibold text-ink text-[22px] m-0">
        Permissions — {group?.name || groupId}
      </h1>
      <p className="text-muted text-[12px] mt-0.5 mb-3">
        Click a tier to set the group's default for that module. Click the
        active marker to clear the grant (the module becomes hidden for any
        user whose only access came from this group).
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
