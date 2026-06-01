import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

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
      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Instructions</h2>
        <p className="text-sm text-gray-600">
          Instructions for this procedure aren't online yet — please call our
          office at <a href="tel:2402522140" className="underline">240-252-2140</a>.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Instructions</h2>
      <ul className="divide-y divide-gray-100">
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
      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Consent forms</h2>
        <p className="text-sm text-gray-600">
          Signed consent forms will appear here once everyone has signed.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Consent forms</h2>
      <ul className="divide-y divide-gray-100">
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
      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Receipts</h2>
        <p className="text-sm text-gray-600">
          Receipts for your payments will appear here.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Receipts</h2>
      <ul className="divide-y divide-gray-100">
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

export default function Documents() {
  const { sid } = useParams()
  const { data, isLoading } = useQuery({
    queryKey: ['portal-documents', sid],
    queryFn: () => portalApi.get(`/${sid}/documents`).then(r => r.data),
    staleTime: 30_000,
  })
  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Documents</h1>
      <InstructionsCard sid={sid} instructions={data.instructions} />
      <ConsentDocsCard sid={sid} consents={data.consents} />
      <ReceiptsCard receipts={data.receipts} />
    </div>
  )
}
