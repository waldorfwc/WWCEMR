import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'
import { QrCode, RotateCcw } from 'lucide-react'

export default function AdminReputationProfiles() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(null)   // null | {id?, display_name, role_label, user_email}
  const [showQrFor, setShowQrFor] = useState(null)   // profile id

  const { data, isLoading } = useQuery({
    queryKey: ['reputation-profiles'],
    queryFn: () => api.get('/admin/reputation/profiles').then(r => r.data),
  })

  const save = useMutation({
    mutationFn: async (t) => {
      if (t.id) return api.patch(`/admin/reputation/profiles/${t.id}`,
                                       { display_name: t.display_name,
                                          role_label: t.role_label,
                                          user_email: t.user_email,
                                          location: t.location,
                                          active: t.active }).then(r => r.data)
      return api.post('/admin/reputation/profiles', {
        display_name: t.display_name,
        role_label: t.role_label,
        user_email: t.user_email,
        location: t.location,
      }).then(r => r.data)
    },
    onSuccess: (saved) => {
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['reputation-profiles'] })
      // If this is a brand-new profile, immediately show its QR
      if (saved?.id && !showQrFor) setShowQrFor(saved.id)
    },
  })

  const rotate = useMutation({
    mutationFn: (pid) => api.post(`/admin/reputation/profiles/${pid}/rotate-token`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reputation-profiles'] }),
  })

  const toggleActive = useMutation({
    mutationFn: ({ id, active }) => api.patch(`/admin/reputation/profiles/${id}`,
                                                       { active }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reputation-profiles'] }),
  })

  if (isLoading) return <LoadingState />
  const profiles = data?.profiles || []

  return (
    <div className="p-4 max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">Reputation Profiles</h1>
        <button onClick={() => setEditing({ display_name: '', role_label: '', user_email: '' })}
                 className="btn-primary text-sm">+ New employee</button>
      </div>

      {profiles.length === 0 ? (
        <div className="text-sm text-muted">
          No profiles yet. Add an employee to generate their QR code.
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y">
          {profiles.map(p => (
            <div key={p.id} className="px-4 py-3 flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.display_name}</span>
                  {!p.active && (
                    <span className="text-[10px] bg-gray-200 text-gray-700 rounded px-2 py-0.5">
                      Inactive
                    </span>
                  )}
                </div>
                {p.role_label && (
                  <div className="text-xs text-muted">{p.role_label}</div>
                )}
                {p.location && (
                  <div className="text-xs text-muted">
                    📍 {p.location === 'white_plains' ? 'White Plains'
                          : p.location === 'arlington' ? 'Arlington'
                          : p.location === 'brandywine' ? 'Brandywine'
                          : p.location}
                  </div>
                )}
                {p.user_email && (
                  <div className="text-xs text-muted">{p.user_email}</div>
                )}
                <div className="text-[11px] text-muted mt-1 font-mono">
                  token: {p.qr_token}
                </div>
              </div>
              <div className="flex flex-col items-end gap-1.5 shrink-0">
                <button className="btn-secondary text-xs flex items-center gap-1"
                         onClick={() => setShowQrFor(showQrFor === p.id ? null : p.id)}>
                  <QrCode size={12} /> {showQrFor === p.id ? 'Hide' : 'QR code'}
                </button>
                <button className="text-xs text-gray-700 hover:underline"
                         onClick={() => setEditing({ ...p })}>Edit</button>
                <button className="text-xs text-gray-700 hover:underline flex items-center gap-1"
                         onClick={() => confirm('Rotate the QR token? Old QR codes will stop working.')
                                            && rotate.mutate(p.id)}>
                  <RotateCcw size={11} /> Rotate token
                </button>
                <button className="text-xs text-gray-700 hover:underline"
                         onClick={() => toggleActive.mutate({ id: p.id, active: !p.active })}>
                  {p.active ? 'Deactivate' : 'Reactivate'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* QR display modal */}
      {showQrFor && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
              onClick={() => setShowQrFor(null)}>
          <div className="bg-white rounded-lg shadow-lg p-6 max-w-md w-full text-center"
                onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-1">
              {profiles.find(p => p.id === showQrFor)?.display_name}
            </h2>
            {profiles.find(p => p.id === showQrFor)?.role_label && (
              <div className="text-sm text-muted mb-4">
                {profiles.find(p => p.id === showQrFor)?.role_label}
              </div>
            )}
            <img src={`/api/admin/reputation/profiles/${showQrFor}/qr.png`}
                  alt="QR code"
                  className="w-64 h-64 mx-auto" />
            <div className="flex justify-center gap-2 mt-4">
              <a href={`/api/admin/reputation/profiles/${showQrFor}/qr.png`}
                  download={`qr_${profiles.find(p => p.id === showQrFor)?.display_name || 'qr'}.png`}
                  className="btn-secondary text-sm">Download</a>
              <button onClick={() => window.print()}
                       className="btn-primary text-sm">Print</button>
              <button onClick={() => setShowQrFor(null)}
                       className="btn-secondary text-sm">Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Create/edit modal */}
      {editing && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-lg p-4 max-w-md w-full">
            <h2 className="text-lg font-semibold mb-3">
              {editing.id ? 'Edit profile' : 'New profile'}
            </h2>
            <label className="text-sm font-medium block">Display name</label>
            <input value={editing.display_name}
                    onChange={e => setEditing({...editing, display_name: e.target.value})}
                    placeholder="e.g. Sarah Smith, RN"
                    className="w-full text-sm rounded border-gray-300 mb-3" />
            <label className="text-sm font-medium block">Role</label>
            <input value={editing.role_label || ''}
                    onChange={e => setEditing({...editing, role_label: e.target.value})}
                    placeholder="e.g. Surgical Coordinator"
                    className="w-full text-sm rounded border-gray-300 mb-3" />
            <label className="text-sm font-medium block">Location</label>
            <select value={editing.location || ''}
                     onChange={e => setEditing({...editing, location: e.target.value || null})}
                     className="w-full text-sm rounded border-gray-300 mb-3">
              <option value="">— select —</option>
              <option value="white_plains">White Plains</option>
              <option value="arlington">Arlington</option>
              <option value="brandywine">Brandywine</option>
            </select>
            <div className="text-xs text-muted mb-3">
              Determines which Google review URL 5-star reviewers see.
            </div>
            <label className="text-sm font-medium block">Email (optional)</label>
            <input value={editing.user_email || ''}
                    onChange={e => setEditing({...editing, user_email: e.target.value})}
                    placeholder="optional"
                    className="w-full text-sm rounded border-gray-300" />
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)}
                       className="btn-secondary text-sm">Cancel</button>
              <button onClick={() => save.mutate(editing)}
                       disabled={!editing.display_name?.trim() || save.isPending}
                       className="btn-primary text-sm">
                {save.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
