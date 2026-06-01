import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

export default function MessagesSection({ sid }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const [picked, setPicked] = useState('')

  const { data: thread } = useQuery({
    queryKey: ['staff-thread', sid],
    queryFn: () => api.get(`/staff/surgeries/${sid}/messages`).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })

  const { data: templates } = useQuery({
    queryKey: ['message-templates'],
    queryFn: () => api.get('/staff/message-templates').then(r => r.data),
    staleTime: 300_000,
  })

  async function insertTemplate(tid) {
    if (!tid) return
    setPicked(tid)
    try {
      const { data } = await api.get(
        `/staff/message-templates/${tid}/render?surgery_id=${sid}`)
      setDraft(data.body)
    } finally {
      setPicked('')
    }
  }

  const send = useMutation({
    mutationFn: (body) =>
      api.post(`/staff/surgeries/${sid}/messages`, { body }).then(r => r.data),
    onSuccess: () => {
      setDraft('')
      qc.invalidateQueries({ queryKey: ['staff-thread', sid] })
      qc.invalidateQueries({ queryKey: ['staff-inbox'] })
    },
  })

  const messages = thread?.messages || []
  return (
    <section id="messages" className="card mt-4">
      <h2 className="text-lg font-semibold mb-3">Messages</h2>
      <div className="max-h-80 overflow-y-auto space-y-2 mb-3 pr-1">
        {messages.length === 0 && (
          <div className="text-sm text-muted text-center py-4">
            No messages yet.
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} className="text-sm border-l-2 pl-2"
                style={{ borderColor: m.author_kind === 'staff'
                                          ? '#7c3aed' : '#6b7280' }}>
            <div className="text-xs text-muted">
              {m.author_kind === 'staff' ? (m.author_email || 'WWC') : 'Patient'}
              {' · '}{m.sent_at?.slice(0, 16).replace('T', ' ')}
            </div>
            <div className="whitespace-pre-wrap">{m.body}</div>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 mb-2">
        <select value={picked}
                  onChange={e => insertTemplate(e.target.value)}
                  className="text-xs rounded border-gray-300">
          <option value="">Insert template…</option>
          {(templates?.templates || []).map(t => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
      </div>
      <textarea value={draft}
                  onChange={e => setDraft(e.target.value)}
                  disabled={send.isPending}
                  rows={3}
                  placeholder="Reply to patient…"
                  className="w-full text-sm rounded border-gray-300" />
      <div className="flex justify-end mt-2">
        <button onClick={() => draft.trim() && send.mutate(draft.trim())}
                 disabled={!draft.trim() || send.isPending}
                 className="btn-primary text-sm">
          {send.isPending ? 'Sending…' : 'Send'}
        </button>
      </div>
    </section>
  )
}
