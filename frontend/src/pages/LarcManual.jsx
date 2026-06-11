import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, BookOpen, ChevronRight, Edit3, Plus, Save, Trash2, X } from 'lucide-react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { MODULE, TIER } from '../routes.jsx'


function renderMarkdown(md) {
  const raw = marked.parse(md || '', { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw)
}


export default function LarcManual() {
  const qc = useQueryClient()
  const { tier } = useCurrentUser()
  const canEdit = tier(MODULE.LARC, TIER.MANAGE)
  const [editingId, setEditingId] = useState(null)
  const [adding, setAdding] = useState(false)

  const { data: sections = [], isLoading } = useQuery({
    queryKey: ['larc-manual'],
    queryFn: () => api.get('/larc/manual').then(r => r.data),
  })

  const toc = useMemo(
    () => sections.map(s => ({ id: s.id, slug: s.slug, title: s.title })),
    [sections]
  )

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <Link to="/larc" className="text-muted hover:text-plum-700">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
              <BookOpen size={22} className="text-plum-700" />
              LARC Operating Manual
            </h1>
            <div className="text-muted text-[12px] mt-0.5">
              Working rules for the WWC LARC inventory + tracking workflow.
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
          onSaved={() => { setEditingId(null); qc.invalidateQueries({ queryKey: ['larc-manual'] }) }}
          onDeleted={() => qc.invalidateQueries({ queryKey: ['larc-manual'] })}
        />
      ))}

      {adding && (
        <AddSectionForm
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); qc.invalidateQueries({ queryKey: ['larc-manual'] }) }}
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

  const save = useMutation({
    mutationFn: () => api.patch(`/larc/manual/${section.id}`, {
      title, body_md: body, sort_order: Number(sortOrder),
    }).then(r => r.data),
    onSuccess: onSaved,
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const remove = useMutation({
    mutationFn: () => api.delete(`/larc/manual/${section.id}`),
    onSuccess: onDeleted,
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  if (!editing) {
    return (
      <section id={section.slug} className="card mb-4 scroll-mt-24">
        <div className="flex items-baseline justify-between mb-2 gap-2">
          <h2 className="font-serif text-[18px] font-semibold text-ink m-0">
            {section.title}
          </h2>
          {canEdit && (
            <button onClick={onStartEdit}
                    className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
              <Edit3 size={11} /> Edit
            </button>
          )}
        </div>
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
        <div className="text-[10px] text-gray-400 mt-3">
          Last edited {fmt.date(section.updated_at?.slice(0, 10))}
          {section.updated_by && section.updated_by !== 'system:seed'
            && ` by ${section.updated_by.split('@')[0]}`}
        </div>
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
          <button onClick={() => { if (confirm(`Delete section "${section.title}"?`)) remove.mutate() }}
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


function AddSectionForm({ onClose, onSaved }) {
  const [slug, setSlug] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [sortOrder, setSortOrder] = useState(1000)
  const create = useMutation({
    mutationFn: () => api.post('/larc/manual', {
      slug, title, body_md: body, sort_order: Number(sortOrder),
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
