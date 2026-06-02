import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { MessageSquare, Send } from 'lucide-react'
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
    <div className="px-6 md:px-10 py-8 md:py-10 max-w-3xl">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Patient portal
        </div>
        <h1 className="font-serif text-[32px] md:text-[40px] text-plum-ink font-semibold tracking-tight leading-tight">
          Messages
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          A private channel with your care team. We usually reply the same day.
        </p>
      </header>

      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm
                            max-h-[60vh] overflow-y-auto p-5 space-y-3">
        {messages.length === 0 && (
          <div className="text-[13px] text-plum-700/70 text-center py-10 flex flex-col items-center">
            <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 mb-3">
              <MessageSquare size={18} />
            </div>
            No messages yet. Send us a note below to start the conversation.
          </div>
        )}
        {messages.map(m => {
          const mine = m.author_kind === 'patient'
          return (
            <div key={m.id} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-[13px] shadow-sm ${
                mine
                  ? 'bg-plum-700 text-white rounded-br-sm'
                  : 'bg-plum-50 text-plum-ink rounded-bl-sm border border-plum-100'
              }`}>
                <div className={`text-[10px] mb-1 ${mine ? 'text-white/70' : 'text-plum-600/70'}`}>
                  {m.author_label} · {m.sent_at?.slice(0, 16).replace('T', ' ')}
                </div>
                <div className="whitespace-pre-wrap leading-relaxed">{m.body}</div>
              </div>
            </div>
          )
        })}
        <div ref={scrollRef} />
      </section>

      {!isStaffPreview() && (
        <form
          onSubmit={e => { e.preventDefault(); if (draft.trim()) send.mutate(draft.trim()) }}
          className="bg-white rounded-2xl border border-plum-100 shadow-sm p-4 mt-4 space-y-3">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            disabled={send.isPending}
            placeholder="Type a message…"
            rows={3}
            className="w-full rounded-xl border-plum-100 text-[13px] focus:border-plum-400 focus:ring-plum-200" />
          <div className="flex justify-end">
            <button type="submit"
                     disabled={!draft.trim() || send.isPending}
                     className="btn-primary text-sm inline-flex items-center gap-1.5">
              <Send size={12} /> {send.isPending ? 'Sending…' : 'Send'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
