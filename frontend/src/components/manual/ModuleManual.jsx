import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, BookOpen, ChevronRight, Edit3, Plus, Save, Trash2, X } from 'lucide-react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import api, { fmt } from '../../utils/api'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { TIER } from '../../routes.jsx'

const MANUAL_STALE_AFTER_DAYS = 180

function renderMarkdown(md) {
  const raw = marked.parse(md || '', { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw)
}

function isStale(updated_at) {
  if (!updated_at) return false
  return (Date.now() - new Date(updated_at).getTime()) > MANUAL_STALE_AFTER_DAYS * 864e5
}


/**
 * Shared operating manual component. Used by LARC and Pellet manual routes
 * (and any future module manual). Fetches from /api/manual?module=<module>
 * and posts/patches/deletes via /api/manual and /api/manual/{id}.
 *
 * Props:
 *   module    – backend module slug, e.g. "device_larc" or "pellets"
 *   title     – page heading
 *   blurb     – sub-heading description line
 *   backTo    – breadcrumb link path (default "/")
 *   backLabel – breadcrumb link label (default "Back")
 */
export default function ModuleManual({
  module,
  title,
  blurb,
  backTo = '/',
  backLabel = 'Back',
}) {
  const qc = useQueryClient()
  const { tier } = useCurrentUser()
  const canEdit = tier(module, TIER.MANAGE)
  const [editingId, setEditingId] = useState(null)
  const [adding, setAdding] = useState(false)

  const queryKey = ['manual', module]

  const { data: sections = [], isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get('/manual', { params: { module } }).then(r => r.data),
  })

  const toc = useMemo(
    () => sections.map(s => ({ id: s.id, slug: s.slug, title: s.title, updated_at: s.updated_at })),
    [sections]
  )

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <Link to={backTo} aria-label={backLabel} title={backLabel}
                className="text-muted hover:text-plum-700">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
              <BookOpen size={22} className="text-plum-700" />
              {title}
            </h1>
            <div className="text-muted text-[12px] mt-0.5">
              {blurb}
              {canEdit && ' Click any section to edit.'}
            </div>
          </div>
        </div>
        {canEdit && (
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setAdding(true)}>
            <Plus size={13} /> Add section
          </button>
        )}
      </div>

      {/* TOC */}
      {toc.length > 0 && (
        <div className="card !p-3 mb-4 bg-plum-50/30">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-2">Jump to</div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-[12px]">
            {toc.map(s => (
              <a key={s.id} href={`#${s.slug}`}
                 className="text-plum-700 hover:underline flex items-center gap-1">
                <ChevronRight size={11} /> {s.title}
                {isStale(s.updated_at) && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 ml-1">Review</span>
                )}
              </a>
            ))}
          </div>
        </div>
      )}

      {isLoading && <div className="text-gray-400 italic">Loading…</div>}

      {sections.map(s => (
        <Section
          key={s.id}
          section={s}
          canEdit={canEdit}
          editing={editingId === s.id}
          onStartEdit={() => setEditingId(s.id)}
          onCancel={() => setEditingId(null)}
          onSaved={() => { setEditingId(null); qc.invalidateQueries({ queryKey }) }}
          onDeleted={() => qc.invalidateQueries({ queryKey })}
        />
      ))}

      {adding && (
        <AddSectionForm
          module={module}
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); qc.invalidateQueries({ queryKey }) }}
        />
      )}

      <div className="text-[11px] text-gray-500 text-center mt-8 mb-12">
        Edit this manual as your practice rules evolve. Every save is timestamped.
      </div>
    </div>
  )
}


function Section({ section, canEdit, editing, onStartEdit, onCancel, onSaved, onDeleted }) {
  const [title, setTitle] = useState(section.title)
  const [body, setBody] = useState(section.body_md)
  const [sortOrder, setSortOrder] = useState(section.sort_order)
  const [previewing, setPreviewing] = useState(false)

  const stale = isStale(section.updated_at)

  const save = useMutation({
    mutationFn: () => api.patch(`/manual/${section.id}`, {
      title, body_md: body, sort_order: Number(sortOrder),
    }).then(r => r.data),
    onSuccess: onSaved,
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const remove = useMutation({
    mutationFn: () => api.delete(`/manual/${section.id}`),
    onSuccess: onDeleted,
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  if (!editing) {
    return (
      <section id={section.slug} className="card mb-4 scroll-mt-24">
        <div className="flex items-baseline justify-between mb-2 gap-2">
          <h2 className="font-serif text-[18px] font-semibold text-ink m-0 flex items-center gap-2">
            {section.title}
            {stale && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">Review</span>
            )}
          </h2>
          {canEdit && (
            <button onClick={onStartEdit}
                    className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
              <Edit3 size={11} /> Edit
            </button>
          )}
        </div>
        {section.updated_at && (
          <div className="text-[10px] text-gray-400 mb-2">
            Updated {fmt.date(section.updated_at)}
            {section.updated_by && section.updated_by !== 'system:seed'
              && ` by ${section.updated_by.split('@')[0]}`}
          </div>
        )}
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
             dangerouslySetInnerHTML={{ __html: renderMarkdown(section.body_md) }} />
      </section>
    )
  }

  return (
    <section id={section.slug} className="card mb-4 border-2 border-plum-300 scroll-mt-24">
      <div className="space-y-2">
        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-2">
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Title</label>
            <input className="input text-sm w-full" value={title}
                   onChange={e => setTitle(e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Sort order</label>
            <input type="number" className="input text-sm w-full" value={sortOrder}
                   onChange={e => setSortOrder(e.target.value)} />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <label className="text-[11px] uppercase text-gray-500">Body (Markdown)</label>
          <button onClick={() => setPreviewing(p => !p)}
                  className="text-[10px] text-plum-700 hover:underline">
            {previewing ? 'Edit ✎' : 'Preview 👁'}
          </button>
        </div>
        {previewing ? (
          <div className="border border-border-subtle rounded p-3 bg-white min-h-[200px]
                          prose prose-sm max-w-none text-[13px]
                          [&_table]:border-collapse [&_th]:bg-plum-50 [&_th]:px-2 [&_th]:py-1 [&_th]:border [&_td]:border [&_td]:px-2 [&_td]:py-1"
               dangerouslySetInnerHTML={{ __html: renderMarkdown(body) }} />
        ) : (
          <textarea className="input text-[12px] w-full font-mono"
                    rows={14}
                    value={body}
                    onChange={e => setBody(e.target.value)}
                    placeholder="Markdown supported: # headings · **bold** · *italic* · - lists · | tables | · `code` · > quotes" />
        )}
        <div className="flex items-center justify-between">
          <button onClick={() => { if (window.confirm(`Delete section "${section.title}"?`)) remove.mutate() }}
                  className="text-[11px] text-red-700 hover:underline flex items-center gap-1">
            <Trash2 size={10} /> Delete section
          </button>
          <div className="flex gap-1">
            <button onClick={onCancel}
                    className="text-sm text-muted hover:underline">Cancel</button>
            <button onClick={() => save.mutate()}
                    disabled={save.isPending || !title.trim()}
                    className="btn-primary text-sm flex items-center gap-1">
              <Save size={12} /> {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}


function AddSectionForm({ module, onClose, onSaved }) {
  const [slug, setSlug] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [sortOrder, setSortOrder] = useState(1000)
  const create = useMutation({
    mutationFn: () => api.post('/manual', {
      module, slug, title, body_md: body, sort_order: Number(sortOrder),
    }).then(r => r.data),
    onSuccess: onSaved,
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-border-subtle flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">New Manual Section</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Title *</label>
              <input className="input text-sm w-full" value={title} required
                     onChange={e => { setTitle(e.target.value)
                                       if (!slug) setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')) }} />
            </div>
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Slug (URL anchor)</label>
              <input className="input text-sm w-full font-mono" value={slug} required
                     onChange={e => setSlug(e.target.value)} />
            </div>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Sort order</label>
            <input type="number" className="input text-sm w-full" value={sortOrder}
                   onChange={e => setSortOrder(e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Body (Markdown)</label>
            <textarea className="input text-[12px] w-full font-mono" rows={12}
                      value={body} onChange={e => setBody(e.target.value)} />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button onClick={onClose} className="text-sm text-muted hover:underline">Cancel</button>
          <button onClick={() => create.mutate()}
                  disabled={!title.trim() || !slug.trim() || create.isPending}
                  className="btn-primary text-sm">
            {create.isPending ? 'Adding…' : 'Add section'}
          </button>
        </div>
      </div>
    </div>
  )
}
