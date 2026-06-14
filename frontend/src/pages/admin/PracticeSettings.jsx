import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Check, Shield } from 'lucide-react'
import api from '../../utils/api'


export default function PracticeSettings({ embedded = false }) {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-practice-settings'],
    queryFn: () => api.get('/admin/practice-settings').then(r => r.data),
  })

  // Save-on-blur per field. We track local draft separately from server
  // value so a typo can be reverted without round-tripping.
  const [drafts, setDrafts] = useState({})
  const [flashes, setFlashes] = useState({})

  function flash(key, kind, text, ttl = 1500) {
    setFlashes(p => ({ ...p, [key]: { kind, text } }))
    setTimeout(() => setFlashes(p => {
      const next = { ...p }; delete next[key]; return next
    }), ttl)
  }

  // Hydrate draft state once data lands. Re-hydrate after a successful
  // save so the input shows the canonical (trimmed/null-coerced) value.
  useEffect(() => {
    if (!data) return
    setDrafts(prev => {
      const next = { ...prev }
      for (const s of data.settings) {
        if (!(s.key in next)) next[s.key] = s.value ?? ''
      }
      return next
    })
  }, [data])

  const save = useMutation({
    mutationFn: ({ key, value }) =>
      api.put(`/admin/practice-settings/${key}`, { value }).then(r => r.data),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: ['admin-practice-settings'] })
      flash(vars.key, 'ok', '✓ saved')
    },
    onError: (err, vars) => {
      flash(vars.key, 'err', `✗ ${err?.response?.data?.detail || 'error'}`, 3500)
    },
  })

  const grouped = useMemo(() => {
    if (!data) return []
    const order = []
    const map = new Map()
    for (const s of data.settings) {
      if (!map.has(s.group)) { map.set(s.group, []); order.push(s.group) }
      map.get(s.group).push(s)
    }
    return order.map(g => ({ group: g, fields: map.get(g) }))
  }, [data])

  if (isLoading) return <div className="p-6 text-muted">Loading…</div>
  if (error) return (
    <div className="p-6 text-sm text-red-700">
      {error?.response?.data?.detail || error.message}
    </div>
  )

  function onBlur(key, original) {
    const v = drafts[key] ?? ''
    if (v === (original ?? '')) return  // no change, skip the PUT
    save.mutate({ key, value: v })
  }

  return (
    <div className={embedded ? '' : 'max-w-3xl'}>
      {!embedded && (
        <>
          <Link to="/admin"
                className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
            <ArrowLeft size={12} /> Back to Admin
          </Link>
          <div className="mb-4">
            <h1 className="font-serif font-semibold text-ink text-[22px] m-0">
              Practice Settings
            </h1>
            <p className="text-muted text-[12px] mt-0.5">
              Practice-wide identity fields used to prefill enrollment forms
              (LARC pharmacy orders, future consents). Edits save on blur.
              <span className="inline-flex items-center gap-1 ml-2 text-plum-700">
                <Shield size={11} /> Super Admin only
              </span>
            </p>
          </div>
        </>
      )}

      {grouped.map(({ group, fields }) => (
        <div key={group} className="card mb-4">
          <h2 className="text-sm font-semibold text-ink mb-3">{group}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
            {fields.map(f => {
              const flash = flashes[f.key]
              return (
                <div key={f.key}>
                  <label className="text-[11px] text-gray-600 flex items-center gap-1.5">
                    {f.label}
                    {flash && (
                      <span className={
                        flash.kind === 'ok'
                          ? 'text-[10px] text-success inline-flex items-center gap-0.5'
                          : 'text-[10px] text-danger'
                      }>
                        {flash.kind === 'ok' && <Check size={9} />} {flash.text}
                      </span>
                    )}
                  </label>
                  <input
                    type="text"
                    className="input text-sm w-full mt-0.5"
                    value={drafts[f.key] ?? ''}
                    onChange={e => setDrafts(d => ({ ...d, [f.key]: e.target.value }))}
                    onBlur={() => onBlur(f.key, f.value)}
                    placeholder={f.value ? '' : '—'}
                  />
                  {f.help && (
                    <div className="text-[10px] text-gray-500 mt-0.5">{f.help}</div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}

      <div className="text-[11px] text-muted mt-4">
        Empty a field and tab away to clear it. Every change is audit-logged
        (action <span className="font-mono">PRACTICE_SETTING_UPDATED</span>).
      </div>
    </div>
  )
}
