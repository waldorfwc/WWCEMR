import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi, isStaffPreview } from '../../lib/portal-api'

export default function Messages() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const scrollRef = useRef(null)

  const { data } = useQuery({
    queryKey: ['portal-messages', sid],
    queryFn: () => portalApi.get(`/${sid}/messages`).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [data?.messages?.length])

  const send = useMutation({
    mutationFn: (body) =>
      portalApi.post(`/${sid}/messages`, { body }).then(r => r.data),
    onSuccess: () => {
      setDraft('')
      qc.invalidateQueries({ queryKey: ['portal-messages', sid] })
    },
  })

  const messages = data?.messages || []
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold text-gray-900">Messages</h1>
      <section className="bg-white rounded-lg shadow p-4 max-h-[60vh]
                            overflow-y-auto space-y-3">
        {messages.length === 0 && (
          <div className="text-sm text-gray-500 text-center py-8">
            No messages yet. Send us a message below to start the conversation.
          </div>
        )}
        {messages.map(m => (
          <div key={m.id}
                className={`flex ${m.author_kind === 'patient'
                                       ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm
                                ${m.author_kind === 'patient'
                                    ? 'bg-plum-100 text-gray-900'
                                    : 'bg-gray-100 text-gray-900'}`}>
              <div className="text-[10px] text-gray-500 mb-1">
                {m.author_label} · {m.sent_at?.slice(0, 16).replace('T', ' ')}
              </div>
              <div className="whitespace-pre-wrap">{m.body}</div>
            </div>
          </div>
        ))}
        <div ref={scrollRef} />
      </section>

      {!isStaffPreview() && (
        <form
          onSubmit={e => { e.preventDefault();
                              if (draft.trim()) send.mutate(draft.trim()) }}
          className="bg-white rounded-lg shadow p-4 space-y-2">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            disabled={send.isPending}
            placeholder="Type a message…"
            rows={3}
            className="w-full rounded border-gray-300 text-sm" />
          <div className="flex justify-end">
            <button type="submit"
                     disabled={!draft.trim() || send.isPending}
                     className="btn-primary text-sm">
              {send.isPending ? 'Sending…' : 'Send'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
