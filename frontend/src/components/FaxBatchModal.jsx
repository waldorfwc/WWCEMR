import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

const MODES = [
  { value: 'separate', label: 'Separate', help: 'One fax per doc' },
  { value: 'combined', label: 'Combined', help: 'Merge into one fax' },
  { value: 'by_type',  label: 'By doc type', help: 'Group by category' },
]

export default function FaxBatchModal({
  chartNumber, docIds, defaultDestFax, defaultCover, onClose,
}) {
  const [dest, setDest] = useState(defaultDestFax || '2402522141')
  const [mode, setMode] = useState('separate')
  const [cover, setCover] = useState(defaultCover || '')
  const [result, setResult] = useState(null)
  const queryClient = useQueryClient()

  const send = useMutation({
    mutationFn: () => api.post('/fax/send-batch', {
      chart_number: chartNumber,
      doc_ids: docIds,
      dest_fax: dest,
      grouping_mode: mode,
      cover_text: cover,
    }).then(r => r.data),
    onSuccess: (data) => {
      setResult(data)
      queryClient.invalidateQueries({ queryKey: ['fax-by-chart', chartNumber] })
      queryClient.invalidateQueries({ queryKey: ['fax-recent'] })
    },
  })

  const busy = send.isPending
  const hasResult = !!result

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}
    >
      <div className="bg-white rounded-lg border border-border-subtle w-[520px] p-5">
        <h2 className="font-serif text-lg text-ink m-0">
          Fax {docIds.length} doc{docIds.length === 1 ? '' : 's'} to EMA
        </h2>
        <div className="text-[13px] text-muted mt-0.5">Chart {chartNumber}</div>

        {!hasResult ? (
          <>
            <div className="mt-4">
              <label className="eyebrow block mb-1">Destination fax</label>
              <input className="input" value={dest}
                     onChange={(e) => setDest(e.target.value)} disabled={busy} />
            </div>

            <div className="mt-3">
              <label className="eyebrow block mb-1">Grouping</label>
              <div className="flex gap-2">
                {MODES.map(({ value, label, help }) => (
                  <label key={value}
                         className={`flex-1 p-2 rounded border cursor-pointer text-[13px] ${
                           mode === value
                             ? 'border-plum-700 bg-plum-100'
                             : 'border-border-subtle hover:border-plum-300'
                         }`}>
                    <input type="radio" className="hidden"
                           checked={mode === value}
                           onChange={() => setMode(value)}
                           disabled={busy || (docIds.length === 1 && value !== 'separate')} />
                    <div className="font-medium text-ink">{label}</div>
                    <div className="text-muted text-[11px]">{help}</div>
                  </label>
                ))}
              </div>
            </div>

            <div className="mt-3">
              <label className="eyebrow block mb-1">Cover note</label>
              <textarea className="input" rows={3} value={cover}
                        onChange={(e) => setCover(e.target.value)} disabled={busy} />
            </div>

            {send.isError && (
              <div className="mt-3 text-[12px] text-danger">
                {send.error?.response?.data?.detail || 'Send failed'}
              </div>
            )}

            <div className="mt-4 flex gap-2 justify-end">
              <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
              <button className="btn-primary" onClick={() => send.mutate()} disabled={busy || !dest}>
                {busy ? 'Sending...' : `Send${docIds.length > 1 ? ` ${docIds.length}` : ''}`}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="mt-4 text-[13px]">
              {result.faxes.map((f, i) => (
                <div key={i}
                     className={`flex justify-between py-1.5 border-b border-plum-100 last:border-b-0 ${
                       f.status === 'failed' ? 'text-danger' : 'text-ink'
                     }`}>
                  <span>
                    {f.status === 'failed' ? `✗ ${f.error || 'failed'}` : `✓ sent`}
                    <span className="text-muted ml-2">
                      ({f.doc_ids.length} doc{f.doc_ids.length === 1 ? '' : 's'})
                    </span>
                  </span>
                  <span className="text-muted">{f.ringcentral_message_id || ''}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
