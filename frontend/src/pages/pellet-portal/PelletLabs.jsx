import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { FlaskConical, CheckCircle2 } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

export default function PelletLabs() {
  const qc = useQueryClient()
  const [completed, setCompleted] = useState(false)
  const [drawnDate, setDrawnDate] = useState('')
  const [err, setErr] = useState('')

  const report = useMutation({
    mutationFn: () => pelletPortalApi.post('/labs', {
      completed: true,
      ...(drawnDate ? { drawn_date: drawnDate } : {}),
    }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-dashboard'] }),
    onError: (e) => {
      const detail = e?.response?.data?.detail
      setErr(typeof detail === 'string' ? detail : 'Save failed. Please try again.')
    },
  })

  const done = report.isSuccess

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Report Your Labs
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Let us know once you've completed your lab work. Results come back
          to us automatically.
        </p>
      </header>

      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
        <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 mb-4">
          <FlaskConical size={20} />
        </div>

        {done ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-[14px] text-emerald-800 bg-emerald-50
                              border border-emerald-200 rounded-lg px-4 py-3">
              <CheckCircle2 size={16} />
              Reported — awaiting staff review.
            </div>
            <Link to="/pellet-portal/home" className="btn-secondary text-sm inline-block">
              Back to Checklist
            </Link>
          </div>
        ) : (
          <>
            <label className="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" checked={completed}
                     onChange={e => { setErr(''); setCompleted(e.target.checked) }}
                     className="mt-0.5 h-4 w-4 rounded border-plum-300 text-plum-700
                                  focus:ring-plum-200" />
              <span className="text-[13px] text-plum-ink">
                I have completed my labs.
              </span>
            </label>

            <label className="block mt-5">
              <span className="text-[11px] uppercase tracking-wide text-plum-700/70 font-medium">
                Date Drawn (Optional)
              </span>
              <input type="date" value={drawnDate}
                     onChange={e => setDrawnDate(e.target.value)}
                     className="mt-1 block w-full max-w-xs rounded-lg border border-plum-200
                                  bg-white px-3 py-2 text-sm text-plum-ink
                                  focus:border-plum-500 focus:ring-2 focus:ring-plum-200
                                  focus:outline-none" />
            </label>

            <div className="mt-5 flex items-center gap-3">
              <button onClick={() => { setErr(''); report.mutate() }}
                      disabled={!completed || report.isPending}
                      className="btn-primary text-sm">
                {report.isPending ? 'Saving…' : 'Submit'}
              </button>
              <Link to="/pellet-portal/home"
                    className="text-[12px] text-plum-700 hover:text-plum-900 underline">
                Back to Checklist
              </Link>
            </div>
            {err && <div className="text-xs text-rose-700 mt-3">{err}</div>}
          </>
        )}
      </section>
    </div>
  )
}
