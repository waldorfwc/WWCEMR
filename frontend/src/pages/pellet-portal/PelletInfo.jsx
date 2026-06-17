import { useQuery } from '@tanstack/react-query'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

function renderMarkdown(md) {
  const raw = marked.parse(md || '', { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw)
}

export default function PelletInfo() {
  const infoQ = useQuery({
    queryKey: ['pellet-info'],
    queryFn: () => pelletPortalApi.get('/info').then(r => r.data),
    staleTime: 60_000,
  })

  const infoText = infoQ.data?.info_text || ''

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Rules & Info
        </h1>
      </header>

      {infoQ.isLoading ? (
        <div className="py-16 text-center text-plum-600/70 text-sm">Loading…</div>
      ) : infoQ.error ? (
        <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
          We couldn't load this page right now. Please refresh, or call
          our office at <strong>240-252-2140</strong>.
        </div>
      ) : (
        <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
          <div className="prose prose-sm max-w-none text-[13px] leading-relaxed text-gray-800
                            [&>h1]:font-serif [&>h2]:font-serif [&>h3]:font-serif
                            [&>blockquote]:border-l-4 [&>blockquote]:border-plum-300
                            [&>blockquote]:bg-plum-50/30 [&>blockquote]:py-1 [&>blockquote]:px-3 [&>blockquote]:my-2
                            [&>blockquote]:text-gray-700
                            [&>table]:my-3 [&>table]:text-[12px] [&>th]:bg-plum-50 [&>th]:px-2 [&>th]:py-1 [&>td]:px-2 [&>td]:py-1
                            [&_table]:border-collapse [&_table]:my-3 [&_table]:text-[12px]
                            [&_th]:bg-plum-50 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:border [&_th]:border-border-subtle
                            [&_td]:px-2 [&_td]:py-1 [&_td]:border [&_td]:border-border-subtle
                            [&_code]:bg-gray-100 [&_code]:px-1 [&_code]:rounded [&_code]:text-[12px]
                            [&_strong]:font-semibold
                            [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-2
                            [&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:my-2"
               dangerouslySetInnerHTML={{ __html: renderMarkdown(infoText) }} />
          {!infoText && (
            <div className="text-plum-600/70 text-sm">No information available yet.</div>
          )}
        </section>
      )}

      <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
