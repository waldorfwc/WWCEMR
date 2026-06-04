import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

export default function StaffMessageTemplates() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(null)   // null | {id?, name, body}

  const { data, isLoading } = useQuery({
    queryKey: ['message-templates'],
    queryFn: () => api.get('/staff/message-templates').then(r => r.data),
  })

  const save = useMutation({
    mutationFn: async (t) => {
      if (t.id) {
        return api.put(`/staff/message-templates/${t.id}`,
                          { name: t.name, body: t.body }).then(r => r.data)
      }
      return api.post('/staff/message-templates',
                          { name: t.name, body: t.body }).then(r => r.data)
    },
    onSuccess: () => {
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['message-templates'] })
    },
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/staff/message-templates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['message-templates'] }),
  })

  if (isLoading) return <div className="p-4 text-sm text-muted">Loading…</div>
  const rows = data?.templates || []

  return (
    <div className="p-4 max-w-4xl">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">Message Templates</h1>
        <button onClick={() => setEditing({ name: '', body: '' })}
                 className="btn-primary text-sm">+ New</button>
      </div>

      {rows.length === 0 ? (
        <div className="text-sm text-muted">No templates yet.</div>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y">
          {rows.map(t => (
            <div key={t.id}
                  className="px-4 py-3 flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{t.name}</div>
                <div className="text-xs text-muted truncate">{t.body}</div>
              </div>
              <div className="flex gap-2 shrink-0">
                <button className="btn-secondary text-xs"
                         onClick={() => setEditing({ ...t })}>Edit</button>
                <button className="text-xs px-2 py-1 rounded border border-red-200 text-red-700 hover:bg-red-50"
                         onClick={() => confirm(`Delete "${t.name}"?`)
                                            && del.mutate(t.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-lg p-4 max-w-2xl w-full">
            <h2 className="text-lg font-semibold mb-3">
              {editing.id ? 'Edit template' : 'New template'}
            </h2>
            <label className="text-sm font-medium block mt-2">Name</label>
            <input value={editing.name}
                    onChange={e => setEditing({...editing, name: e.target.value})}
                    className="w-full text-sm rounded border-gray-300 mb-3" />
            <label className="text-sm font-medium block">Body</label>
            <textarea value={editing.body}
                       onChange={e => setEditing({...editing, body: e.target.value})}
                       rows={6}
                       className="w-full text-sm rounded border-gray-300" />
            <div className="text-xs text-muted mt-1">
              Supports <code>{'{{patient_name}}'}</code> and{' '}
              <code>{'{{surgery_date}}'}</code> substitutions.
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)}
                       className="btn-secondary text-sm">Cancel</button>
              <button onClick={() => save.mutate(editing)}
                       disabled={!editing.name.trim() || !editing.body.trim()
                                    || save.isPending}
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
