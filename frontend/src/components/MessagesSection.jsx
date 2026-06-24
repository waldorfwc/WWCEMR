import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { MessageSquare, AlertTriangle, Lock, MailOpen } from 'lucide-react'
import api from '../utils/api'

export default function MessagesSection({ sid, flat = false }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const [picked, setPicked] = useState('')
  const [internal, setInternal] = useState(false)
  // Once the staff explicitly marks the thread unread, stop the auto-read
  // effect from immediately re-marking it read while the panel stays open.
  const suppressAutoRead = useRef(false)

  const { data: thread } = useQuery({
    queryKey: ['staff-thread', sid],
    queryFn: () => api.get(`/staff/surgeries/${sid}/messages`).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })

  // Marking patient messages read is an explicit POST (backend GET has no
  // side effects — Fable M3). When the open thread still has unread patient
  // messages, fire it once so read_by_staff_at gets set and the shared
  // Messages badge clears; then refresh the inbox. Gated on an actual unread
  // message so the 30s thread refetch doesn't re-POST after everything's read.
  const markRead = useMutation({
    mutationFn: () =>
      api.post(`/staff/surgeries/${sid}/messages/mark-read`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['staff-inbox'] }),
  })
  useEffect(() => {
    if (!thread?.messages || suppressAutoRead.current) return
    const hasUnreadPatientMsg = thread.messages.some(
      m => m.author_kind === 'patient' && !m.read_by_staff_at)
    if (hasUnreadPatientMsg && !markRead.isPending) {
      markRead.mutate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread])

  // Flip the thread back to unread so it returns to the Messages inbox/badge.
  const markUnread = useMutation({
    mutationFn: () =>
      api.post(`/staff/surgeries/${sid}/messages/mark-unread`).then(r => r.data),
    onSuccess: () => {
      suppressAutoRead.current = true   // don't let auto-read undo it this view
      qc.invalidateQueries({ queryKey: ['staff-thread', sid] })
      qc.invalidateQueries({ queryKey: ['staff-inbox'] })
    },
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
    mutationFn: ({ body, internal }) =>
      api.post(`/staff/surgeries/${sid}/messages`, { body, internal }).then(r => r.data),
    onSuccess: () => {
      setDraft('')
      setInternal(false)
      qc.invalidateQueries({ queryKey: ['staff-thread', sid] })
      qc.invalidateQueries({ queryKey: ['staff-inbox'] })
    },
  })

  const messages = thread?.messages || []
  const hasPatientMsg = messages.some(m => m.author_kind === 'patient')
  const wrapClass = flat ? '' : 'card mt-4'
  const Wrapper = flat ? 'div' : 'section'
  const titleClass = flat ? 'text-sm font-semibold text-gray-800' : 'text-lg font-semibold'
  return (
    <Wrapper id="messages" className={wrapClass}>
      <div className="flex items-center justify-between mb-3">
        <h3 className={`flex items-center gap-1.5 ${titleClass}`}>
          <MessageSquare size={14} className="text-plum-700" /> Messages
        </h3>
        {hasPatientMsg && (
          <button
            onClick={() => markUnread.mutate()}
            disabled={markUnread.isPending}
            title="Flag this thread to follow up — it returns to the Messages inbox."
            className="flex items-center gap-1 text-xs text-gray-500 hover:text-plum-700">
            <MailOpen size={13} /> Mark unread
          </button>
        )}
      </div>
      <div className="max-h-80 overflow-y-auto space-y-2 mb-3 pr-1">
        {messages.length === 0 && (
          <div className="text-sm text-muted text-center py-4">
            No messages yet.
          </div>
        )}
        {messages.map(m => (
          <div key={m.id}
                className={`text-sm border-l-2 pl-2 ${
                  m.internal ? 'bg-amber-50 rounded-r py-1 pr-2' : ''}`}
                style={{ borderColor: m.internal ? '#d97706'
                          : m.author_kind === 'staff' ? '#7c3aed' : '#6b7280' }}>
            <div className="text-xs text-muted flex items-center gap-1">
              {m.internal && (
                <span className="inline-flex items-center gap-0.5 text-amber-700 font-medium">
                  <Lock size={11} /> Internal · not visible to patient ·
                </span>
              )}
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
                  placeholder={internal
                    ? 'Internal note — the patient will NOT see this…'
                    : 'Reply to patient…'}
                  className={`w-full text-sm rounded border-gray-300 ${
                    internal ? 'bg-amber-50' : ''}`} />
      <div className="flex items-center justify-between gap-2 mt-2">
        <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer select-none">
          <input type="checkbox" checked={internal}
                  onChange={e => setInternal(e.target.checked)}
                  className="rounded border-gray-300 text-amber-600 focus:ring-amber-500" />
          <Lock size={12} className={internal ? 'text-amber-700' : 'text-gray-400'} />
          Internal note (patient can&rsquo;t see)
        </label>
        <div className="flex items-center gap-2">
          {!internal && thread && thread.can_notify === false && (
            <span className="flex items-center gap-1 text-xs text-amber-700"
                  title="The reply is saved to the portal thread, but the patient won't get the 'new message' SMS.">
              <AlertTriangle size={13} />
              {thread.notify_block === 'no_phone'
                ? "Won't notify — no phone on file"
                : "Won't notify — no SMS consent"}
            </span>
          )}
          <button
            onClick={() => draft.trim() && send.mutate({ body: draft.trim(), internal })}
            disabled={!draft.trim() || send.isPending}
            className="btn-primary text-sm">
            {send.isPending ? 'Saving…' : internal ? 'Add Note' : 'Send'}
          </button>
        </div>
      </div>
    </Wrapper>
  )
}
