import { useQuery } from '@tanstack/react-query'
import { FileText, Download } from 'lucide-react'
import { larcPortalApi } from '../../lib/larc-portal-api'

export default function LarcPortalDocuments() {
  const docsQ = useQuery({
    queryKey: ['larc-portal-documents'],
    queryFn: () => larcPortalApi.get('/documents').then(r => r.data),
    staleTime: 30_000,
  })

  if (docsQ.isLoading) {
    return (
      <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm
                       text-[13px] text-plum-600/70">
        Loading…
      </div>
    )
  }

  if (docsQ.isError) {
    return (
      <div className="bg-white rounded-2xl border border-rose-200 p-6 shadow-sm text-[13px] text-rose-700">
        We couldn't load your documents right now. Please refresh, or call our
        office at <strong>240-252-2140</strong>.
      </div>
    )
  }

  // Be defensive about the document shape: each row may be
  // { label, url } / { name, url } / something else entirely.
  const documents = docsQ.data?.documents || []

  return (
    <div className="space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Device Tracking
        </div>
        <h1 className="font-serif text-[22px] md:text-[26px] text-plum-ink font-semibold tracking-tight leading-tight">
          Documents
        </h1>
        <p className="text-[13px] text-plum-700/80 mt-2 max-w-xl">
          Forms and paperwork related to your device, all in one place.
        </p>
      </header>

      {documents.length === 0 ? (
        <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm">
          <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
            No Documents Yet
          </h2>
          <p className="text-[13px] text-plum-700/80 mt-1">
            Your documents will appear here once they're ready.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {documents.map((d, i) => {
            const label = d.label || d.name || d.filename || 'Document'
            const url = d.url || d.download_url
            return (
              <li key={d.id ?? url ?? i}
                  className="bg-white rounded-2xl border border-plum-100 p-5 shadow-sm
                              hover:shadow-md transition flex items-center justify-between gap-4">
                <div className="flex items-center gap-4 min-w-0 flex-1">
                  <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                    <FileText size={20} />
                  </div>
                  <div className="min-w-0 font-serif text-[16px] text-plum-ink font-semibold leading-tight truncate">
                    {label}
                  </div>
                </div>
                {url && (
                  <a href={url} target="_blank" rel="noreferrer"
                     className="btn-secondary text-sm inline-flex items-center gap-1 shrink-0">
                    <Download size={12} /> Download
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      )}

      <div className="text-[11px] text-plum-600/70 text-center pt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
