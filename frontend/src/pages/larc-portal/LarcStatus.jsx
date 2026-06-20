import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { larcPortalApi } from '../../lib/larc-portal-api'

const TRACK_LABELS = {
  pharmacy:       'Pharmacy Order',
  practice_owned: 'In-Stock Device',
}

export default function LarcStatus() {
  const dashQ = useQuery({
    queryKey: ['larc-portal-dash'],
    queryFn: () => larcPortalApi.get('/dashboard').then(r => r.data),
    staleTime: 30_000,
  })

  if (dashQ.isLoading) {
    return (
      <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm
                       text-[13px] text-plum-600/70">
        Loading…
      </div>
    )
  }

  if (dashQ.isError) {
    return (
      <div className="bg-white rounded-2xl border border-rose-200 p-6 shadow-sm
                       text-[13px] text-rose-700">
        Something went wrong loading your status. Please try again.
      </div>
    )
  }

  const data = dashQ.data || {}
  const steps = data.steps || []
  const trackLabel = TRACK_LABELS[data.track] || 'Device Tracking'

  // Next-action hint based on payment / enrollment state.
  const payment = data.payment || {}
  const enrollment = data.enrollment || {}
  const paymentDue = Number(payment.responsibility) > 0 && !payment.paid
  const enrollmentDue =
    enrollment.required &&
    enrollment.status !== 'completed' &&
    enrollment.status !== 'signed'

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-plum-100 p-4 md:p-6 shadow-sm">
        <h1 className="font-serif text-[18px] md:text-[20px] text-plum-ink font-semibold tracking-tight">
          {trackLabel}
        </h1>
        <p className="text-[12px] text-plum-600/70 mt-0.5">
          Track your device through each step below.
        </p>

        <ol className="mt-5">
          {steps.map((step, i) => {
            const status = step.status || 'upcoming'
            const isDone = status === 'done'
            const isCurrent = status === 'current'
            const isUpcoming = !isDone && !isCurrent
            const isLast = i === steps.length - 1
            return (
              <li key={step.key} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <div className={`w-9 h-9 rounded-full grid place-items-center text-[12px] font-semibold transition shrink-0 ${
                    isDone
                      ? 'bg-plum-700 text-white shadow-lg shadow-plum-300/50'
                      : isCurrent
                        ? 'bg-white border-2 border-plum-700 text-plum-700 ring-4 ring-plum-100'
                        : 'bg-plum-50 border border-plum-100 text-plum-400'
                  }`}>
                    {isDone ? '✓' : (i + 1)}
                  </div>
                  {!isLast && (
                    <div className={`w-px flex-1 my-1 min-h-[24px] ${
                      isDone ? 'bg-plum-300' : 'bg-plum-100'
                    }`} />
                  )}
                </div>
                <div className={`pt-1.5 pb-4 text-[13px] ${
                  isUpcoming ? 'text-plum-400' : 'text-plum-700 font-medium'
                }`}>
                  {step.label}
                  {isCurrent && (
                    <span className="ml-2 text-[10px] uppercase tracking-[0.16em]
                                       text-plum-700 bg-plum-50 px-2 py-0.5 rounded-full
                                       border border-plum-100 align-middle">
                      Now
                    </span>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      </div>

      {(paymentDue || enrollmentDue) && (
        <div className="bg-white rounded-2xl border border-amber-200 p-4 md:p-6 shadow-sm">
          <div className="text-[11px] uppercase tracking-[0.16em] text-amber-800 font-semibold">
            Next Step
          </div>
          <ul className="mt-2 space-y-2">
            {paymentDue && (
              <li>
                <Link to="/larc-portal/home/payments"
                      className="inline-flex items-center gap-1.5 text-[13px] font-semibold
                                 text-plum-700 hover:text-plum-900">
                  Payment due →
                </Link>
              </li>
            )}
            {enrollmentDue && (
              <li>
                <Link to="/larc-portal/home/enrollment"
                      className="inline-flex items-center gap-1.5 text-[13px] font-semibold
                                 text-plum-700 hover:text-plum-900">
                  Sign enrollment →
                </Link>
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}
