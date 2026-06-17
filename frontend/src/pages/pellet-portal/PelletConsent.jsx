import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ClipboardCheck, CheckCircle2, Mail } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

export default function PelletConsent() {
  const qc = useQueryClient()
  const [err, setErr] = useState('')

  const sign = useMutation({
    mutationFn: () => pelletPortalApi.post('/consent').then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-dashboard'] }),
    onError: (e) => {
      const detail = e?.response?.data?.detail
      setErr(typeof detail === 'string'
        ? detail
        : 'We couldn’t send your consent form just now. Please call our office at 240-252-2140.')
    },
  })

  const status = sign.data?.status
  const alreadyValid = status === 'already_valid'
  const sent = status === 'sent'

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Insertion Consent
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Sign your pellet insertion consent form. We'll email you a secure
          link to review and sign.
        </p>
      </header>

      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
        <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 mb-4">
          <ClipboardCheck size={20} />
        </div>

        {alreadyValid ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-[14px] text-emerald-800 bg-emerald-50
                              border border-emerald-200 rounded-lg px-4 py-3">
              <CheckCircle2 size={16} />
              Your consent is already on file.
            </div>
            <Link to="/pellet-portal/home" className="btn-secondary text-sm inline-block">
              Back to Checklist
            </Link>
          </div>
        ) : sent ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-[14px] text-sky-800 bg-sky-50
                              border border-sky-200 rounded-lg px-4 py-3">
              <Mail size={16} />
              We've sent your consent form — check your email to sign.
            </div>
            <Link to="/pellet-portal/home" className="btn-secondary text-sm inline-block">
              Back to Checklist
            </Link>
          </div>
        ) : (
          <>
            <p className="text-[13px] text-plum-700/80 max-w-md">
              Click below and we'll send the consent form to the email we have
              on file. It takes about three minutes to sign.
            </p>
            <div className="mt-5 flex items-center gap-3">
              <button onClick={() => { setErr(''); sign.mutate() }}
                      disabled={sign.isPending}
                      className="btn-primary text-sm">
                {sign.isPending ? 'Sending…' : 'Sign Insertion Consent'}
              </button>
              <Link to="/pellet-portal/home"
                    className="text-[12px] text-plum-700 hover:text-plum-900 underline">
                Back to Checklist
              </Link>
            </div>
            {err && <div className="text-sm text-rose-700 mt-3">{err}</div>}
          </>
        )}
      </section>
    </div>
  )
}
