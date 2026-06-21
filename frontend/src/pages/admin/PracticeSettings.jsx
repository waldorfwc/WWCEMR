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

      <ProvidersSection />

      <div className="text-[11px] text-muted mt-4">
        Empty a field and tab away to clear it. Every change is audit-logged
        (action <span className="font-mono">PRACTICE_SETTING_UPDATED</span>).
      </div>
    </div>
  )
}

const ROLE_LABELS = { provider: 'Provider', app: 'APP' }
const CREDENTIALS = ['MD', 'DO', 'NP', 'PA']

function ProvidersSection() {
  const qc = useQueryClient()
  const { data: clinicians, isLoading } = useQuery({
    queryKey: ['clinicians'],
    queryFn: () => api.get('/admin/users/clinicians').then(r => r.data),
  })

  const [form, setForm] = useState({
    display_name: '',
    email: '',
    npi: '',
    clinician_role: 'provider',
    credential: 'MD',
  })

  function set(key, value) {
    setForm(f => ({ ...f, [key]: value }))
  }

  const addProvider = useMutation({
    mutationFn: () =>
      api.post('/admin/users', {
        email: form.email.trim(),
        display_name: form.display_name.trim(),
        group: 'clinical',
        npi: form.npi.trim(),
        clinician_role: form.clinician_role,
        credential: form.credential,
      }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['clinicians'] })
      setForm({
        display_name: '',
        email: '',
        npi: '',
        clinician_role: 'provider',
        credential: 'MD',
      })
    },
    onError: (e) => {
      alert(e?.response?.data?.detail || 'Could not add provider')
    },
  })

  const canAdd =
    form.display_name.trim() && form.email.trim() && form.npi.trim() &&
    !addProvider.isPending

  return (
    <div className="card mb-4">
      <h2 className="text-sm font-semibold text-ink mb-3">Providers</h2>

      <div className="mb-4">
        {isLoading ? (
          <div className="text-[12px] text-muted">Loading…</div>
        ) : !clinicians || clinicians.length === 0 ? (
          <div className="text-[12px] text-muted">No providers yet.</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {clinicians.map(c => (
              <div key={c.email}
                   className="flex items-center justify-between py-1.5 text-sm">
                <div className="text-ink">
                  {c.display_name}
                  {c.credential && (
                    <span className="text-gray-500">, {c.credential}</span>
                  )}
                  <span className="text-[11px] text-muted ml-2">
                    {ROLE_LABELS[c.clinician_role] || c.clinician_role}
                  </span>
                </div>
                <div className="text-[11px] text-muted font-mono">
                  NPI {c.npi || '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-gray-100 pt-3">
        <div className="text-[11px] font-semibold text-gray-600 mb-2">
          Add Provider
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
          <div>
            <label className="text-[11px] text-gray-600">Display Name</label>
            <input
              type="text"
              className="input text-sm w-full mt-0.5"
              value={form.display_name}
              onChange={e => set('display_name', e.target.value)}
              placeholder="Dr. Jane Smith"
            />
          </div>
          <div>
            <label className="text-[11px] text-gray-600">Email</label>
            <input
              type="email"
              className="input text-sm w-full mt-0.5"
              value={form.email}
              onChange={e => set('email', e.target.value)}
              placeholder="jsmith@waldorfwomenscare.com"
            />
          </div>
          <div>
            <label className="text-[11px] text-gray-600">NPI</label>
            <input
              type="text"
              className="input text-sm w-full mt-0.5"
              value={form.npi}
              onChange={e => set('npi', e.target.value)}
              placeholder="1234567890"
            />
          </div>
          <div>
            <label className="text-[11px] text-gray-600">Role</label>
            <select
              className="input text-sm w-full mt-0.5"
              value={form.clinician_role}
              onChange={e => set('clinician_role', e.target.value)}
            >
              <option value="provider">Provider</option>
              <option value="app">APP</option>
            </select>
          </div>
          <div>
            <label className="text-[11px] text-gray-600">Credential</label>
            <select
              className="input text-sm w-full mt-0.5"
              value={form.credential}
              onChange={e => set('credential', e.target.value)}
            >
              {CREDENTIALS.map(cr => (
                <option key={cr} value={cr}>{cr}</option>
              ))}
            </select>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-primary text-sm mt-3"
          disabled={!canAdd}
          onClick={() => addProvider.mutate()}
        >
          {addProvider.isPending ? 'Adding…' : 'Add Provider'}
        </button>
      </div>
    </div>
  )
}
