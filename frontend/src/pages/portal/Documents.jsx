import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi, isStaffPreview } from '../../lib/portal-api'
import StepUpPayFlow from '../../components/portal/StepUpPayFlow'

function fmtMoney(v) {
  return `$${Number(v).toFixed(2)}`
}

function PdfDownloadButton({ url, filename, label }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(false)
  async function go() {
    setBusy(true); setErr(false)
    try {
      const r = await portalApi.get(url, { responseType: 'blob' })
      const blobUrl = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = filename
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(blobUrl)
    } catch (e) {
      setErr(true)
    } finally { setBusy(false) }
  }
  return (
    <div>
      <button onClick={go} disabled={busy} className="btn-secondary text-sm">
        {busy ? 'Loading…' : label}
      </button>
      {err && (
        <div className="text-xs text-red-600 mt-1">
          Not available — please call our office at{' '}
          <a href="tel:2402522140" className="underline">240-252-2140</a>.
        </div>
      )}
    </div>
  )
}

function InstructionsCard({ sid, instructions }) {
  if (instructions === null) {
    return (
      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
        <h2 className="font-serif text-[17px] text-plum-ink font-semibold tracking-tight mb-3">Instructions</h2>
        <p className="text-sm text-gray-600">
          Instructions for this procedure aren't online yet — please call our
          office at <a href="tel:2402522140" className="underline">240-252-2140</a>.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
      <h2 className="font-serif text-[17px] text-plum-ink font-semibold tracking-tight mb-3">Instructions</h2>
      <ul className="divide-y divide-plum-50">
        <li className="py-2 flex items-center justify-between">
          <span className="text-sm text-gray-800">Pre-op instructions</span>
          <PdfDownloadButton
            url={`/${sid}/documents/instructions/preop`}
            filename="preop_instructions.pdf"
            label="Download" />
        </li>
        <li className="py-2 flex items-center justify-between">
          <span className="text-sm text-gray-800">Post-op instructions</span>
          <PdfDownloadButton
            url={`/${sid}/documents/instructions/postop`}
            filename="postop_instructions.pdf"
            label="Download" />
        </li>
      </ul>
    </section>
  )
}

function ConsentDocsCard({ sid, consents }) {
  if (!consents?.length) {
    return (
      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
        <h2 className="font-serif text-[17px] text-plum-ink font-semibold tracking-tight mb-3">Consent forms</h2>
        <p className="text-sm text-gray-600">
          Signed consent forms will appear here once everyone has signed.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
      <h2 className="font-serif text-[17px] text-plum-ink font-semibold tracking-tight mb-3">Consent forms</h2>
      <ul className="divide-y divide-plum-50">
        {consents.map(c => (
          <li key={c.envelope_id}
              className="py-2 flex items-center justify-between gap-3">
            <span className="text-sm text-gray-800 truncate">
              {c.template_name}
            </span>
            <PdfDownloadButton
              url={`/${sid}/consent/signed-pdf/${c.envelope_id}`}
              filename={`${c.template_name.replace(/[^a-z0-9]/gi, '_')}.pdf`}
              label="Download" />
          </li>
        ))}
      </ul>
    </section>
  )
}

function ReceiptsCard({ receipts }) {
  if (!receipts?.length) {
    return (
      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
        <h2 className="font-serif text-[17px] text-plum-ink font-semibold tracking-tight mb-3">Receipts</h2>
        <p className="text-sm text-gray-600">
          Receipts for your payments will appear here.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
      <h2 className="font-serif text-[17px] text-plum-ink font-semibold tracking-tight mb-3">Receipts</h2>
      <ul className="divide-y divide-plum-50">
        {receipts.map(r => (
          <li key={r.id}
              className="py-2 flex items-center justify-between text-sm">
            <span>{(r.paid_at || '').slice(0, 10)}</span>
            <span className="text-gray-900">{fmtMoney(r.amount)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function LabsAppointmentCard({ sid, labs, refetchDocs }) {
  const [date, setDate] = useState(labs?.appointment_date || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  if (!labs?.scheduled_date) return null   // no surgery date yet; nothing to schedule against

  // Compute 4-7 day window from surgery date
  const base = new Date(labs.scheduled_date + 'T00:00:00')
  const earliest = new Date(base); earliest.setDate(earliest.getDate() - 7)
  const latest   = new Date(base); latest.setDate(latest.getDate() - 4)
  const isoE = earliest.toISOString().slice(0, 10)
  const isoL = latest.toISOString().slice(0, 10)
  const longFmt = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString(
    undefined, { weekday: 'long', month: 'long', day: 'numeric' })

  async function save() {
    setBusy(true); setErr('')
    try {
      await portalApi.post(`/${sid}/self-report/lab-appointment-date`,
                            { date: date || null })
      refetchDocs?.()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Save failed.')
    } finally { setBusy(false) }
  }

  return (
    <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">Pre-Op Lab Appointment</h2>
        <span className={`text-xs px-2 py-1 rounded ${
          labs.appointment_date
            ? 'bg-green-100 text-green-700'
            : 'bg-amber-100 text-amber-700'
        }`}>
          {labs.appointment_date ? 'scheduled' : 'needed'}
        </span>
      </div>
      <p className="text-sm text-gray-700">
        Please schedule your pre-op labs between{' '}
        <strong>{longFmt(isoE)}</strong> and <strong>{longFmt(isoL)}</strong>{' '}
        (4–7 days before your surgery). After you book, enter the date below
        so our office knows you're set.
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <input type="date"
               min={isoE} max={isoL}
               value={date}
               onChange={e => setDate(e.target.value)}
               className="border border-gray-300 rounded px-2 py-1 text-sm" />
        <button onClick={save} disabled={busy || (date === (labs.appointment_date || ''))}
                className="btn-primary text-sm">
          {busy ? 'Saving…' : labs.appointment_date ? 'Update' : 'Save'}
        </button>
        {labs.appointment_date && (
          <button onClick={() => { setDate(''); save() }} disabled={busy}
                  className="text-xs text-gray-500 hover:underline">
            Clear
          </button>
        )}
      </div>
      {labs.appointment_date && (
        <div className="text-xs text-gray-600">
          ✓ Reported: <strong>{longFmt(labs.appointment_date)}</strong>
          {labs.reported_at && (
            <span className="text-gray-400"> · saved {labs.reported_at.slice(0, 10)}</span>
          )}
        </div>
      )}
      {err && <div className="text-xs text-red-600">{err}</div>}
    </section>
  )
}


function ClearanceCard({ sid, clearance, uploads, refetchUploads }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  if (!clearance?.required) return null   // hide entirely

  async function upload() {
    if (!file) return
    setBusy(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('kind', 'clearance')
      await portalApi.post(`/${sid}/clearance/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setFile(null)
      refetchUploads()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Upload failed.')
    } finally { setBusy(false) }
  }

  const statusBadge =
    clearance.status === 'approved'
      ? 'bg-green-100 text-green-700'
      : clearance.status === 'uploaded'
      ? 'bg-amber-100 text-amber-700'
      : 'bg-gray-200 text-gray-700'

  const myClearanceUploads = (uploads || []).filter(u =>
    u.kind === 'clearance' || u.kind === 'ekg'
  )

  return (
    <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">Clearance</h2>
        <span className={`text-xs px-2 py-1 rounded ${statusBadge}`}>
          {clearance.status}
        </span>
      </div>

      <div>
        <div className="text-xs text-gray-500 mb-1">
          Step 1: Download your clearance form
        </div>
        {clearance.form_available ? (
          <PdfDownloadButton
            url={`/${sid}/clearance/generated-form`}
            filename={clearance.form_filename || 'wwc_clearance_form.pdf'}
            label="Download personalized form" />
        ) : (
          <PdfDownloadButton
            url={`/${sid}/clearance/template`}
            filename="wwc_clearance_template.pdf"
            label="Download blank template" />
        )}
      </div>

      {!isStaffPreview() && (
        <div>
          <div className="text-xs text-gray-500 mb-1">
            Step 2: Upload your completed form or EKG (PDF, JPEG, PNG, HEIC, max 10 MB)
          </div>
          <div className="flex items-center gap-2">
            <input type="file"
                    accept="application/pdf,image/jpeg,image/png,image/heic"
                    onChange={e => setFile(e.target.files?.[0] || null)}
                    className="text-xs" />
            <button onClick={upload} disabled={!file || busy}
                     className="btn-primary text-sm">
              {busy ? 'Uploading…' : 'Upload'}
            </button>
          </div>
          {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
        </div>
      )}

      {myClearanceUploads.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Your uploads:</div>
          <ul className="text-sm">
            {myClearanceUploads.map(u => (
              <li key={u.id} className="flex items-center justify-between py-1">
                <span className="truncate mr-2">
                  {u.filename}
                  <span className="text-xs text-gray-500 ml-2">
                    {u.uploaded_at?.slice(0, 10)}
                  </span>
                </span>
                {u.download_url && (
                  <a href={u.download_url} target="_blank" rel="noreferrer"
                      className="btn-secondary text-xs">
                    Download
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function FmlaCard({ sid, fmla, refetchFmla }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [showPay, setShowPay] = useState(false)

  if (!fmla) return null

  async function upload() {
    if (!file) return
    setBusy(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      await portalApi.post(`/${sid}/fmla/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setFile(null)
      refetchFmla()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Upload failed.')
    } finally { setBusy(false) }
  }

  const hasBlank = (fmla.blank_uploads || []).length > 0
  const feePaid  = !!fmla.fee_paid
  const status   = fmla.status || ''

  const badge =
    status === 'completed'   ? 'bg-green-100 text-green-700' :
    status === 'in_review'   ? 'bg-amber-100 text-amber-700' :
    status === 'submitted'   ? 'bg-amber-100 text-amber-700' :
                                 'bg-gray-200 text-gray-700'

  return (
    <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">FMLA paperwork</h2>
        <span className={`text-xs px-2 py-1 rounded ${badge}`}>
          {status || 'not started'}
        </span>
      </div>

      {status === 'completed' && fmla.completed_uploads?.length > 0 && (
        <div>
          <p className="text-sm text-gray-700">
            Your completed FMLA paperwork is ready.
          </p>
          <ul className="text-sm mt-2">
            {fmla.completed_uploads.map(u => (
              <li key={u.id} className="flex items-center justify-between py-1">
                <span className="truncate mr-2">{u.filename}</span>
                {u.download_url && (
                  <a href={u.download_url} target="_blank" rel="noreferrer"
                      className="btn-primary text-xs">Download</a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {status === 'in_review' && (
        <p className="text-sm text-gray-700">
          Your FMLA paperwork is in review.
        </p>
      )}

      {status === 'submitted' && (
        <p className="text-sm text-gray-700">
          ✓ Submitted. We're filling out your form and will have it ready
          within 5 business days.
        </p>
      )}

      {!status && (
        <>
          <p className="text-sm text-gray-600">
            If you need FMLA documentation for work, upload your employer's
            blank form and pay the ${fmla.fee_amount} processing fee.
          </p>

          {!hasBlank && !isStaffPreview() && (
            <div>
              <div className="text-xs text-gray-500 mb-1">
                Step 1: Upload your employer's blank FMLA form
              </div>
              <div className="flex items-center gap-2">
                <input type="file"
                        accept="application/pdf,image/jpeg,image/png,image/heic"
                        onChange={e => setFile(e.target.files?.[0] || null)}
                        className="text-xs" />
                <button onClick={upload} disabled={!file || busy}
                         className="btn-primary text-sm">
                  {busy ? 'Uploading…' : 'Upload'}
                </button>
              </div>
              {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
            </div>
          )}

          {hasBlank && (
            <div className="text-xs text-gray-600">
              ✓ Form received: {fmla.blank_uploads[0].filename}
            </div>
          )}

          {!feePaid && hasBlank && !showPay && !isStaffPreview() && (
            <div>
              <div className="text-xs text-gray-500 mb-1">
                Step 2: Pay the ${fmla.fee_amount} processing fee
              </div>
              <button onClick={() => setShowPay(true)}
                       className="btn-primary text-sm">
                Pay ${fmla.fee_amount}
              </button>
            </div>
          )}

          {!feePaid && hasBlank && showPay && !isStaffPreview() && (
            <StepUpPayFlow
              stepUpUrl={`/${sid}/fmla/step-up`}
              checkoutUrl={`/${sid}/fmla/checkout`}
              onCancel={() => setShowPay(false)} />
          )}

          {feePaid && !hasBlank && (
            <div className="text-sm text-amber-700">
              Payment received — please upload your form to complete your request.
            </div>
          )}
        </>
      )}
    </section>
  )
}

export default function Documents() {
  const { sid } = useParams()
  const { data, isLoading, refetch: refetchDocs } = useQuery({
    queryKey: ['portal-documents', sid],
    queryFn: () => portalApi.get(`/${sid}/documents`).then(r => r.data),
    staleTime: 30_000,
  })
  const { data: uploadsData, refetch: refetchUploads } = useQuery({
    queryKey: ['portal-uploads', sid],
    queryFn: () => portalApi.get(`/${sid}/uploads`).then(r => r.data),
    staleTime: 30_000,
  })
  const { data: fmlaData, refetch: refetchFmla } = useQuery({
    queryKey: ['portal-fmla', sid],
    queryFn: () => portalApi.get(`/${sid}/fmla`).then(r => r.data),
    staleTime: 30_000,
  })
  if (isLoading) {
    return <div className="px-6 md:px-10 py-16 text-plum-600/70 text-sm">Loading…</div>
  }
  return (
    <div className="px-6 md:px-10 py-8 md:py-10 max-w-5xl">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Patient portal
        </div>
        <h1 className="font-serif text-[32px] md:text-[40px] text-plum-ink font-semibold tracking-tight leading-tight">
          Documents
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Pre-op instructions, signed consent forms, receipts, lab and
          clearance uploads — everything in one place.
        </p>
      </header>
      <div className="space-y-4">
        <InstructionsCard sid={sid} instructions={data.instructions} />
        <ConsentDocsCard sid={sid} consents={data.consents} />
        <ReceiptsCard receipts={data.receipts} />
        <LabsAppointmentCard sid={sid} labs={data.labs} refetchDocs={refetchDocs} />
        <ClearanceCard sid={sid}
                         clearance={data.clearance}
                         uploads={uploadsData?.uploads}
                         refetchUploads={refetchUploads} />
        <FmlaCard sid={sid} fmla={fmlaData} refetchFmla={refetchFmla} />
      </div>
    </div>
  )
}
