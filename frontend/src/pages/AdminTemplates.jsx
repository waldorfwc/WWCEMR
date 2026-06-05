import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Plus, Trash2, X, Eye, AlertTriangle, ExternalLink, GraduationCap, ArrowUp, ArrowDown, ArrowUpDown, Search } from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useConfirm } from '../components/ui/ConfirmDialog'


const CATEGORIES = ['clinical', 'admin', 'billing', 'safety', 'compliance', 'communication']


function SortableTh({ label, k, sort, onClick, align = 'left' }) {
  const active = sort.key === k
  const icon = !active
    ? <ArrowUpDown size={10} className="text-gray-300" />
    : sort.dir === 'asc'
      ? <ArrowUp size={11} className="text-plum-700" />
      : <ArrowDown size={11} className="text-plum-700" />
  return (
    <th className={`table-th cursor-pointer select-none hover:bg-plum-100 text-${align}`}
        onClick={() => onClick(k)}>
      <span className="inline-flex items-center gap-1">
        {label}
        {icon}
      </span>
    </th>
  )
}


function targetSummaryText(t) {
  const parts = []
  if (t.assigned_groups?.length) parts.push(t.assigned_groups.map(g => g.name).join(', '))
  if (t.assigned_users?.length)  parts.push(`${t.assigned_users.length} user(s)`)
  if (t.assigned_permission)     parts.push(t.assigned_permission)
  if (!parts.length && t.role)   parts.push(t.role)
  return parts.join(' · ')
}


function LastUpdatedCell({ template }) {
  const iso = template.updated_at
  const who = template.updated_by || template.created_by
  if (!iso) return <span className="text-muted">—</span>
  const d = new Date(iso)
  const dateStr = d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
  const timeStr = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  return (
    <span title={who ? `${dateStr} ${timeStr} · ${who}` : `${dateStr} ${timeStr}`}>
      <span className="text-gray-700">{dateStr}</span>
      {who && (
        <span className="block text-[10px] text-muted">by {who.split('@')[0]}</span>
      )}
    </span>
  )
}
const PRIORITIES = ['low', 'medium', 'high', 'critical']
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const RECURRENCE_KINDS = [
  { v: 'daily',             label: 'Daily',                hint: 'Every day. Sat/Sun skipped automatically.' },
  { v: 'weekdays_of_week',  label: 'Specific weekdays',    hint: 'e.g. Mon/Wed/Fri or just Tue.' },
  { v: 'days_of_month',     label: 'Specific days of month', hint: 'e.g. 1st & 15th of every month.' },
  { v: 'anniversary',       label: 'Yearly anniversary',   hint: 'Once a year on a specific date.' },
  { v: 'every_n_days',      label: 'Every N days',         hint: 'e.g. every 14 days starting from a date.' },
  { v: 'every_n_months',    label: 'Every N months',       hint: 'e.g. every 3 months on the same calendar day.' },
  { v: 'every_n_years',     label: 'Every N years',        hint: 'e.g. every 2 years on the same date.' },
  { v: 'on_demand',         label: 'On demand',            hint: 'Never auto-generated.' },
]

const FOLLOWUP_KINDS = [
  { v: 'none',   label: 'No follow-up — Yes/No is enough' },
  { v: 'count',  label: 'Ask "How many?" (number)' },
  { v: 'reason', label: 'Ask "Why?" (text)' },
]

const EXPIRES_KINDS = [
  { v: 'never',          label: 'Never expires' },
  { v: 'days',           label: 'Days from cert date' },
  { v: 'weeks',          label: 'Weeks from cert date' },
  { v: 'months',         label: 'Months from cert date' },
  { v: 'years',          label: 'Years from cert date' },
  { v: 'specific_date',  label: 'On a specific date (everyone the same)' },
]

const PRIORITY_BADGE = {
  critical: 'bg-red-100 text-red-700 border-red-200',
  high:     'bg-orange-100 text-orange-700 border-orange-200',
  medium:   'bg-blue-50 text-blue-700 border-blue-100',
  low:      'bg-gray-100 text-gray-600 border-gray-200',
}


// Map legacy `frequency` to the new `recurrence_kind` so old templates
// hydrate cleanly into the new builder.
function deriveRecurrenceKind(t) {
  if (t.recurrence_kind) return t.recurrence_kind
  switch (t.frequency) {
    case 'daily':     return 'daily'
    case 'weekly':    return 'weekdays_of_week'
    case 'monthly':   return 'days_of_month'
    case 'annual':    return 'anniversary'
    case 'on_demand': return 'on_demand'
    default:          return 'daily'
  }
}

function summarizeSchedule(t) {
  const k = t.recurrence_kind || deriveRecurrenceKind(t)
  if (k === 'daily') return 'daily (Mon–Fri)'
  if (k === 'on_demand') return 'on demand'
  if (k === 'weekdays_of_week') {
    const ws = t.recurrence_weekdays || (t.weekday != null ? [t.weekday] : [])
    return ws.length ? ws.map(i => WEEKDAYS[i]).join('/') : 'weekdays'
  }
  if (k === 'days_of_month') {
    const ds = t.recurrence_days_of_month || (t.day_of_month != null ? [t.day_of_month] : [])
    return ds.length ? `day${ds.length > 1 ? 's' : ''} ${ds.join(', ')}` : 'monthly'
  }
  if (k === 'anniversary') return `every ${t.anchor_date || 'year'}`
  if (k === 'every_n_days')   return `every ${t.interval_n || '?'} days`
  if (k === 'every_n_months') return `every ${t.interval_n || '?'} months`
  if (k === 'every_n_years')  return `every ${t.interval_n || '?'} years`
  return k
}


export default function AdminTemplates() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [editingId, setEditingId] = useState(searchParams.get('edit') || null)
  const [adding, setAdding] = useState(false)

  // ?edit=<template_id> deep-links from other pages (Training cards, Matrix).
  // Sync the URL with the drawer open/close state so reload-with-URL keeps
  // the drawer open and closing the drawer clears the param.
  useEffect(() => {
    const param = searchParams.get('edit')
    if (param && param !== editingId) setEditingId(param)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  function closeEditor() {
    setEditingId(null)
    const next = new URLSearchParams(searchParams)
    next.delete('edit')
    setSearchParams(next, { replace: true })
  }

  const { data, isLoading } = useQuery({
    queryKey: ['admin-templates', 'with-assignees'],
    queryFn: () => api.get('/checklist/templates?include_assignees=true').then(r => r.data),
  })
  const allTemplates = data?.templates || []

  const [filters, setFilters] = useState({
    category: '', assignee: '', manager: '', title: '',
  })
  const [sort, setSort] = useState({ key: '', dir: '' })
  function toggleSort(key) {
    setSort(prev => {
      if (prev.key !== key)   return { key, dir: 'asc' }
      if (prev.dir === 'asc') return { key, dir: 'desc' }
      return { key: '', dir: '' }
    })
  }

  const templates = (() => {
    const f = filters
    const filt = allTemplates.filter(t => {
      if (f.category && t.category !== f.category) return false
      if (f.title) {
        const hay = `${t.title || ''} ${t.question_text || ''}`.toLowerCase()
        if (!hay.includes(f.title.toLowerCase())) return false
      }
      if (f.manager) {
        const m = (t.escalate_to_email || '').toLowerCase()
        if (!m.includes(f.manager.toLowerCase())) return false
      }
      if (f.assignee) {
        const needle = f.assignee.toLowerCase()
        const emails = (t.assignee_emails || []).join(' ').toLowerCase()
        const groups = (t.assigned_groups || []).map(g => g.name).join(' ').toLowerCase()
        if (!emails.includes(needle) && !groups.includes(needle)) return false
      }
      return true
    })
    if (!sort.key) return filt
    const key = sort.key
    const dir = sort.dir === 'desc' ? -1 : 1
    function val(t) {
      switch (key) {
        case 'title':    return (t.question_text || t.title || '').toLowerCase()
        case 'category': return (t.category || '').toLowerCase()
        case 'manager':  return (t.escalate_to_email || '').toLowerCase()
        case 'targets':  return targetSummaryText(t).toLowerCase()
        case 'updated':  return t.updated_at || ''
        default: return ''
      }
    }
    return [...filt].sort((a, b) => {
      const av = val(a), bv = val(b)
      if (av < bv) return -1 * dir
      if (av > bv) return  1 * dir
      return 0
    })
  })()

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <Link to="/admin" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
            <ArrowLeft size={12} /> Back to Admin
          </Link>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Checklist Templates</h1>
          <p className="text-muted text-[12px] mt-0.5">
            Each active template generates one task instance per scheduled day per assignee.
            Sat/Sun are skipped by default; monthly/anniversary tasks landing on a weekend roll to Monday.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <a href="/admin/training" target="_blank" rel="noreferrer"
              className="btn-secondary text-sm flex items-center gap-1"
              title="Open Training Matrix in a new tab">
            <GraduationCap size={14} /> Training Matrix
            <ExternalLink size={11} className="text-muted" />
          </a>
          <button className="btn-primary text-sm flex items-center gap-1" onClick={() => setAdding(true)}>
            <Plus size={14} /> New Template
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm">
          <div>
            <label className="text-[10px] uppercase text-muted block mb-1">Title contains</label>
            <div className="relative">
              <Search size={11} className="absolute left-2 top-2 text-muted" />
              <input className="input text-sm pl-7 w-full"
                     placeholder="Title / question"
                     value={filters.title}
                     onChange={e => setFilters({ ...filters, title: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-muted block mb-1">Category</label>
            <select className="input text-sm w-full" aria-label="Category filter"
                    value={filters.category}
                    onChange={e => setFilters({ ...filters, category: e.target.value })}>
              <option value="">All categories</option>
              {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-muted block mb-1">Assignee contains</label>
            <input className="input text-sm w-full"
                   placeholder="Email or group name"
                   value={filters.assignee}
                   onChange={e => setFilters({ ...filters, assignee: e.target.value })} />
          </div>
          <div>
            <label className="text-[10px] uppercase text-muted block mb-1">Manager contains</label>
            <input className="input text-sm w-full"
                   placeholder="Escalate-to email"
                   value={filters.manager}
                   onChange={e => setFilters({ ...filters, manager: e.target.value })} />
          </div>
          <div className="flex items-end gap-2">
            <button type="button" className="text-[11px] text-plum-700 hover:underline"
                    onClick={() => {
                      setFilters({ category:'', assignee:'', manager:'', title:'' })
                      setSort({ key:'', dir:'' })
                    }}>
              Clear filters
            </button>
            <span className="text-[11px] text-muted ml-auto">
              {templates.length} of {allTemplates.length}
            </span>
          </div>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <SortableTh label="Title / Question" k="title"    sort={sort} onClick={toggleSort} />
              <SortableTh label="Category"          k="category" sort={sort} onClick={toggleSort} />
              <th className="table-th">Schedule</th>
              <th className="table-th">Due</th>
              <SortableTh label="Manager"           k="manager"  sort={sort} onClick={toggleSort} />
              <SortableTh label="Targets"           k="targets"  sort={sort} onClick={toggleSort} />
              <th className="table-th">Assignees</th>
              <SortableTh label="Last Updated"      k="updated"  sort={sort} onClick={toggleSort} />
              <th className="table-th text-right">Active</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">Loading…</td></tr>
            )}
            {!isLoading && templates.map(t => {
              const unassigned = t.assignee_count === 0
              return (
              <tr key={t.id} className={`table-row cursor-pointer ${
                    unassigned
                      ? 'bg-red-50 hover:bg-red-100/70'
                      : 'hover:bg-plum-50/40'
                  }`}
                  onClick={() => setEditingId(t.id)}>
                <td className="table-td">
                  <div className="font-medium text-[13px] flex items-center gap-1.5">
                    {unassigned && (
                      <span className="text-red-700" title="No one is assigned to this task">
                        <AlertTriangle size={12} />
                      </span>
                    )}
                    {t.question_text || t.title}
                  </div>
                  {t.question_text && t.title !== t.question_text && (
                    <div className="text-[10px] text-muted">{t.title}</div>
                  )}
                  {unassigned && (
                    <div className="text-[10px] text-red-700 font-medium">
                      ⚠ No one is assigned — this task won't generate instances
                    </div>
                  )}
                  {t.followup_kind && t.followup_kind !== 'none' && (
                    <div className="text-[10px] text-amber-700">
                      → If No: {t.followup_prompt || (t.followup_kind === 'count' ? 'how many?' : 'why?')}
                    </div>
                  )}
                </td>
                <td className="table-td text-[11px] capitalize">{t.category}</td>
                <td className="table-td text-[11px]">
                  <span className="capitalize">{summarizeSchedule(t)}</span>
                </td>
                <td className="table-td text-[11px] font-mono">{t.due_time?.slice(0, 5) || '—'}</td>
                <td className="table-td text-[11px] font-mono">
                  {t.escalate_to_email ? (
                    <span title={`Escalates after ${t.escalate_after_hours || 24}h`}>
                      {t.escalate_to_email.split('@')[0]}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="table-td text-[11px]">
                  <TemplateTargetSummary template={t} />
                </td>
                <td className="table-td text-[11px]">
                  <AssigneesCell template={t} />
                </td>
                <td className="table-td text-[11px]">
                  <LastUpdatedCell template={t} />
                </td>
                <td className="table-td text-right">
                  {t.active ? (
                    <span className="text-success text-[10px]">●</span>
                  ) : (
                    <span className="text-muted text-[10px]">○</span>
                  )}
                </td>
              </tr>
              )
            })}
            {!isLoading && templates.length === 0 && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">No templates yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {editingId && (
        <TemplateDrawer templateId={editingId} onClose={closeEditor} />
      )}
      {adding && (
        <TemplateDrawer templateId={null} onClose={() => setAdding(false)} />
      )}
    </div>
  )
}


function AssigneesCell({ template }) {
  // Backend returns assignee_count + assignee_emails when ?include_assignees=true.
  // If those keys are missing (e.g. old client), fall back to a dash.
  const count = template.assignee_count
  const emails = template.assignee_emails || []
  const truncated = template.assignee_truncated

  if (count === undefined) {
    return <span className="text-muted">—</span>
  }
  if (count === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-red-700"
            title="No one will receive this task. Check targeting + training certifications.">
        <AlertTriangle size={12} /> 0 — unassigned
      </span>
    )
  }

  // Show count + first 3 names, hover for the full list (capped at 25)
  const preview = emails.slice(0, 3)
            .map(e => e.split('@')[0])
            .join(', ')
  const tooltip = emails.join('\n') + (truncated ? '\n…' : '')

  return (
    <span className="inline-flex items-center gap-1" title={tooltip}>
      <span className="font-medium">{count}</span>
      <span className="text-muted text-[10px]">— {preview}{count > 3 && '…'}</span>
    </span>
  )
}


function TemplateTargetSummary({ template }) {
  const parts = []
  if (template.assigned_groups?.length) {
    parts.push(template.assigned_groups.map(g => g.name).join(', '))
  }
  if (template.assigned_users?.length) {
    parts.push(`${template.assigned_users.length} user${template.assigned_users.length === 1 ? '' : 's'}`)
  }
  if (template.assigned_permission) {
    parts.push(<code key="p" className="text-plum-700">{template.assigned_permission}</code>)
  }
  if (parts.length === 0 && template.role) {
    return <span className="text-muted italic">{template.role} (legacy)</span>
  }
  if (parts.length === 0) {
    return <span className="text-amber-600 italic">no targets</span>
  }
  return <>{parts.map((p, i) => <span key={i}>{i > 0 && <span className="text-muted"> · </span>}{p}</span>)}</>
}


function TemplateDrawer({ templateId, onClose }) {
  const qc = useQueryClient()
  const isNew = !templateId

  const { data: template } = useQuery({
    queryKey: ['admin-template', templateId],
    queryFn: () => api.get(`/checklist/templates/${templateId}`).then(r => r.data),
    enabled: !isNew,
  })
  const { data: groupsList } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: () => api.get('/admin/groups').then(r => r.data),
  })
  const { data: catalog } = useQuery({
    queryKey: ['perm-catalog'],
    queryFn: () => api.get('/admin/permissions-catalog').then(r => r.data),
  })
  const { data: users } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })

  const [form, setForm] = useState({
    title: '',
    description: '',
    instructions: '',
    category: 'admin',
    // Recurrence (new builder)
    recurrence_kind: 'daily',
    recurrence_weekdays: [0, 1, 2, 3, 4],   // default Mon–Fri for the weekdays kind
    recurrence_days_of_month: [1],
    anchor_date: '',
    interval_n: 1,
    weekend_rule: '',                       // '' = use default
    due_time: '',
    priority: 'medium',
    active: true,
    // Yes/No question
    question_text: '',
    followup_kind: 'none',
    followup_prompt: '',
    // Manager escalation
    escalate_to_email: '',
    escalate_after_hours: 24,
    // Training prerequisite
    requires_training: true,
    training_material_url: '',
    expires_kind: 'never',
    expires_value: 1,
    expires_on_date: '',
    // Targeting
    role: 'custom',
    assigned_group_ids: [],
    assigned_users: [],
    assigned_permission: '',
  })
  const [hydrated, setHydrated] = useState(false)
  const [previewResult, setPreviewResult] = useState(null)

  useEffect(() => {
    if (template && !hydrated) {
      const k = deriveRecurrenceKind(template)
      setForm({
        title: template.title || '',
        description: template.description || '',
        instructions: template.instructions || '',
        category: template.category,
        recurrence_kind: k,
        recurrence_weekdays: template.recurrence_weekdays
          || (template.weekday != null ? [template.weekday] : [0, 1, 2, 3, 4]),
        recurrence_days_of_month: template.recurrence_days_of_month
          || (template.day_of_month != null ? [template.day_of_month] : [1]),
        anchor_date: template.anchor_date || '',
        interval_n: template.interval_n ?? 1,
        weekend_rule: template.weekend_rule || '',
        due_time: (template.due_time || '').slice(0, 5),
        priority: template.priority,
        active: template.active,
        question_text: template.question_text || '',
        followup_kind: template.followup_kind || 'none',
        followup_prompt: template.followup_prompt || '',
        escalate_to_email: template.escalate_to_email || '',
        escalate_after_hours: template.escalate_after_hours ?? 24,
        requires_training: template.requires_training !== false,
        training_material_url: template.training_material_url || '',
        expires_kind: template.expires_kind || 'never',
        expires_value: template.expires_value ?? 1,
        expires_on_date: template.expires_on_date || '',
        role: template.role || 'custom',
        assigned_group_ids: (template.assigned_groups || []).map(g => g.id),
        assigned_users: template.assigned_users || [],
        assigned_permission: template.assigned_permission || '',
      })
      setHydrated(true)
    }
  }, [template, hydrated])

  function updateForm(patch) {
    setForm(prev => ({ ...prev, ...patch }))
  }

  function toggleGroup(id) {
    setForm(prev => ({
      ...prev,
      assigned_group_ids: prev.assigned_group_ids.includes(id)
        ? prev.assigned_group_ids.filter(g => g !== id)
        : [...prev.assigned_group_ids, id],
    }))
  }

  function toggleWeekday(i) {
    setForm(prev => ({
      ...prev,
      recurrence_weekdays: prev.recurrence_weekdays.includes(i)
        ? prev.recurrence_weekdays.filter(x => x !== i)
        : [...prev.recurrence_weekdays, i].sort(),
    }))
  }

  function updateDaysOfMonth(value) {
    // Parse "1, 15" → [1, 15]
    const parts = value.split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
    const nums = parts.map(p => parseInt(p, 10)).filter(n => !Number.isNaN(n) && n >= 1 && n <= 31)
    updateForm({ recurrence_days_of_month: Array.from(new Set(nums)).sort((a, b) => a - b) })
  }

  // Map the new builder back to the legacy `frequency` field so old code
  // that still reads `frequency` keeps working. Prefer the most precise
  // legacy value the kind allows.
  function legacyFrequencyFor(kind) {
    switch (kind) {
      case 'daily':            return 'daily'
      case 'weekdays_of_week': return 'weekly'
      case 'days_of_month':    return 'monthly'
      case 'anniversary':      return 'annual'
      case 'every_n_days':     return 'daily'
      case 'every_n_months':   return 'monthly'
      case 'every_n_years':    return 'annual'
      case 'on_demand':        return 'on_demand'
      default:                 return 'daily'
    }
  }

  const save = useMutation({
    mutationFn: () => {
      const k = form.recurrence_kind
      const body = {
        title: form.title,
        description: form.description || null,
        instructions: form.instructions || null,
        category: form.category,
        // legacy mirror
        frequency: legacyFrequencyFor(k),
        weekday: k === 'weekdays_of_week' && form.recurrence_weekdays.length === 1
          ? form.recurrence_weekdays[0] : null,
        day_of_month: k === 'days_of_month' && form.recurrence_days_of_month.length === 1
          ? form.recurrence_days_of_month[0] : null,
        // new recurrence
        recurrence_kind: k,
        recurrence_weekdays: k === 'weekdays_of_week' ? form.recurrence_weekdays : null,
        recurrence_days_of_month: k === 'days_of_month' ? form.recurrence_days_of_month : null,
        anchor_date: ['anniversary', 'every_n_days', 'every_n_months', 'every_n_years'].includes(k)
          ? (form.anchor_date || null) : null,
        interval_n: ['every_n_days', 'every_n_months', 'every_n_years'].includes(k)
          ? form.interval_n : null,
        weekend_rule: form.weekend_rule || null,
        due_time: form.due_time || null,
        priority: form.priority,
        active: form.active,
        // question
        question_text: form.question_text || null,
        followup_kind: form.followup_kind,
        followup_prompt: form.followup_kind === 'none' ? null : (form.followup_prompt || null),
        // manager
        escalate_to_email: form.escalate_to_email || null,
        escalate_after_hours: form.escalate_after_hours || 24,
        // training
        requires_training: form.requires_training,
        training_material_url: form.training_material_url || null,
        expires_kind: form.expires_kind,
        expires_value: ['days', 'weeks', 'months', 'years'].includes(form.expires_kind)
          ? form.expires_value : null,
        expires_on_date: form.expires_kind === 'specific_date'
          ? (form.expires_on_date || null) : null,
        // targeting
        role: form.role || 'custom',
        assigned_group_ids: form.assigned_group_ids,
        assigned_users: form.assigned_users,
        assigned_permission: form.assigned_permission || null,
      }
      if (isNew) return api.post('/checklist/templates', body).then(r => r.data)
      return api.patch(`/checklist/templates/${templateId}`, body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-templates'] })
      qc.invalidateQueries({ queryKey: ['admin-template', templateId] })
      onClose()
    },
  })

  const remove = useMutation({
    mutationFn: () => api.delete(`/checklist/templates/${templateId}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-templates'] })
      onClose()
    },
  })

  const preview = useMutation({
    mutationFn: () => api.post(`/checklist/templates/${templateId}/preview-assignees`).then(r => r.data),
    onSuccess: (data) => setPreviewResult(data),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-3xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">
            {isNew ? 'New Template' : 'Edit Template'}
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-5">
          {/* Basics */}
          <section className="space-y-3">
            <Field label="Internal title (admin-side label)">
              <input className="input text-sm" value={form.title}
                     onChange={e => updateForm({ title: e.target.value })}
                     placeholder="e.g. Klara messages cleared" />
            </Field>
            <Field label="Description (one line, shown on the task list)">
              <input className="input text-sm" value={form.description}
                     onChange={e => updateForm({ description: e.target.value })} />
            </Field>
            <Field label="Instructions (long form, shown when expanded)">
              <textarea className="input text-sm" rows={2}
                        value={form.instructions}
                        onChange={e => updateForm({ instructions: e.target.value })} />
            </Field>
          </section>

          {/* Yes/No question */}
          <section className="border-t border-gray-100 pt-4">
            <h3 className="text-sm font-semibold text-ink mb-1">Yes/No question</h3>
            <p className="text-[11px] text-muted mb-3">
              The user sees this prompt and answers Yes or No. If they answer No, the
              follow-up below is asked so we capture <em>how many</em> or <em>why</em> —
              never just "uncompleted".
            </p>
            <Field label="Question shown to user (defaults to title if blank)">
              <input className="input text-sm" value={form.question_text}
                     onChange={e => updateForm({ question_text: e.target.value })}
                     placeholder='e.g. "Are all Klara messages completed?"' />
            </Field>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
              <Field label="If No, ask…">
                <select className="input text-sm" value={form.followup_kind}
                        onChange={e => updateForm({ followup_kind: e.target.value })}>
                  {FOLLOWUP_KINDS.map(f => <option key={f.v} value={f.v}>{f.label}</option>)}
                </select>
              </Field>
              {form.followup_kind !== 'none' && (
                <Field label="Follow-up prompt">
                  <input className="input text-sm" value={form.followup_prompt}
                         onChange={e => updateForm({ followup_prompt: e.target.value })}
                         placeholder={form.followup_kind === 'count'
                           ? 'e.g. "How many are left?"'
                           : 'e.g. "Why hasn\'t it been completed?"'} />
                </Field>
              )}
            </div>
          </section>

          {/* Recurrence */}
          <section className="border-t border-gray-100 pt-4">
            <h3 className="text-sm font-semibold text-ink mb-1">Schedule</h3>
            <p className="text-[11px] text-muted mb-3">
              Pick how often this task fires. Sat/Sun are skipped by default for daily +
              specific-weekdays; for monthly / anniversary / every-N, weekend hits roll
              forward to Monday unless you override below.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Recurrence">
                <select className="input text-sm" value={form.recurrence_kind}
                        onChange={e => updateForm({ recurrence_kind: e.target.value })}>
                  {RECURRENCE_KINDS.map(r => <option key={r.v} value={r.v}>{r.label}</option>)}
                </select>
                <div className="text-[10px] text-muted mt-1">
                  {RECURRENCE_KINDS.find(r => r.v === form.recurrence_kind)?.hint}
                </div>
              </Field>

              <Field label="If schedule lands on a weekend">
                <select className="input text-sm" value={form.weekend_rule}
                        onChange={e => updateForm({ weekend_rule: e.target.value })}>
                  <option value="">Default (skip for daily, roll for monthly+)</option>
                  <option value="skip">Skip the weekend hit</option>
                  <option value="roll_to_monday">Roll forward to Monday</option>
                </select>
              </Field>
            </div>

            {/* Per-kind detail */}
            <div className="mt-3 space-y-3">
              {form.recurrence_kind === 'weekdays_of_week' && (
                <Field label="Days of week">
                  <div className="flex gap-1.5">
                    {WEEKDAYS.map((d, i) => {
                      const on = form.recurrence_weekdays.includes(i)
                      return (
                        <button key={d} type="button"
                                onClick={() => toggleWeekday(i)}
                                className={`text-xs w-10 h-9 rounded border ${
                                  on ? 'bg-plum-100 border-plum-300 text-plum-700 font-semibold'
                                     : 'bg-white border-gray-200 text-muted hover:border-plum-300'
                                }`}>
                          {d}
                        </button>
                      )
                    })}
                  </div>
                </Field>
              )}

              {form.recurrence_kind === 'days_of_month' && (
                <Field label="Days of month (comma-separated, 1–31)">
                  <input className="input text-sm font-mono"
                         placeholder="e.g. 1, 15"
                         defaultValue={form.recurrence_days_of_month.join(', ')}
                         onBlur={e => updateDaysOfMonth(e.target.value)} />
                  <div className="text-[10px] text-muted mt-1">
                    Tip: a 31 will only fire in months that have a 31st.
                  </div>
                </Field>
              )}

              {(form.recurrence_kind === 'anniversary'
                || form.recurrence_kind === 'every_n_days'
                || form.recurrence_kind === 'every_n_months'
                || form.recurrence_kind === 'every_n_years') && (
                <div className="grid grid-cols-2 gap-3">
                  <Field label={form.recurrence_kind === 'anniversary' ? 'Anniversary date' : 'Anchor date (start counting from)'}>
                    <input className="input text-sm font-mono" type="date"
                           value={form.anchor_date}
                           onChange={e => updateForm({ anchor_date: e.target.value })} />
                  </Field>
                  {form.recurrence_kind !== 'anniversary' && (
                    <Field label={`Every N ${form.recurrence_kind.replace('every_n_', '')}`}>
                      <input className="input text-sm font-mono" type="number" min="1"
                             value={form.interval_n}
                             onChange={e => updateForm({ interval_n: parseInt(e.target.value || '1', 10) })} />
                    </Field>
                  )}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3">
              <Field label="Category">
                <select className="input text-sm capitalize" value={form.category}
                        onChange={e => updateForm({ category: e.target.value })}>
                  {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Due time (optional)">
                <input className="input text-sm font-mono" type="time"
                       value={form.due_time}
                       onChange={e => updateForm({ due_time: e.target.value })} />
              </Field>
              <Field label="Priority">
                <select className="input text-sm" value={form.priority}
                        onChange={e => updateForm({ priority: e.target.value })}>
                  {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </Field>
            </div>
            <div className="mt-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.active}
                       onChange={e => updateForm({ active: e.target.checked })} />
                Active — generates instances on schedule
              </label>
            </div>
          </section>

          {/* Manager escalation */}
          <section className="border-t border-gray-100 pt-4">
            <h3 className="text-sm font-semibold text-ink mb-1">Accountability</h3>
            <p className="text-[11px] text-muted mb-3">
              When the task isn't answered in time (or is answered No), the listed
              manager gets an email + Slack DM and the task shows up on their Manager
              Dashboard.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Manager (escalate to) *">
                <select className={`input text-sm font-mono ${!form.escalate_to_email ? 'border-red-300 bg-red-50/40' : ''}`}
                        value={form.escalate_to_email}
                        onChange={e => updateForm({ escalate_to_email: e.target.value })}>
                  <option value="">— required: pick a manager —</option>
                  {(users || []).map(u => (
                    <option key={u.email} value={u.email}>
                      {u.display_name || u.email.split('@')[0]} — {u.email}
                    </option>
                  ))}
                </select>
                {!form.escalate_to_email && (
                  <div className="text-[10px] text-red-700 mt-1">
                    Every template must have a manager — they get pain points + escalations.
                  </div>
                )}
              </Field>
              <Field label="Escalate after (hours past due)">
                <input className="input text-sm font-mono" type="number" min="1"
                       value={form.escalate_after_hours}
                       onChange={e => updateForm({ escalate_after_hours: parseInt(e.target.value || '24', 10) })} />
              </Field>
            </div>
          </section>

          {/* Training */}
          <section className="border-t border-gray-100 pt-4">
            <h3 className="text-sm font-semibold text-ink mb-1">Training & certification</h3>
            <p className="text-[11px] text-muted mb-3">
              When required, only users with an active certification on this task receive
              instances. Certifications need a manager-authorized trainer to sign off,
              then the trainee confirms they were trained.
            </p>
            <div className="space-y-3">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.requires_training}
                       onChange={e => updateForm({ requires_training: e.target.checked })} />
                Require training certification before this task can be assigned
              </label>
              {form.requires_training && (
                <>
                  <Field label="Training material (URL — Google Doc, video, SOP)">
                    <input className="input text-sm font-mono"
                           type="url"
                           placeholder="https://docs.google.com/document/d/..."
                           value={form.training_material_url}
                           onChange={e => updateForm({ training_material_url: e.target.value })} />
                  </Field>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <Field label="Certification expires">
                      <select className="input text-sm" value={form.expires_kind}
                              onChange={e => updateForm({ expires_kind: e.target.value })}>
                        {EXPIRES_KINDS.map(e => <option key={e.v} value={e.v}>{e.label}</option>)}
                      </select>
                    </Field>
                    {['days', 'weeks', 'months', 'years'].includes(form.expires_kind) && (
                      <Field label={`How many ${form.expires_kind}?`}>
                        <input className="input text-sm font-mono" type="number" min="1"
                               value={form.expires_value}
                               onChange={e => updateForm({ expires_value: parseInt(e.target.value || '1', 10) })} />
                      </Field>
                    )}
                    {form.expires_kind === 'specific_date' && (
                      <Field label="Expires on date">
                        <input className="input text-sm font-mono" type="date"
                               value={form.expires_on_date}
                               onChange={e => updateForm({ expires_on_date: e.target.value })} />
                      </Field>
                    )}
                  </div>
                </>
              )}
            </div>

            {!isNew && (
              <TrainersAndTrainees templateId={templateId}
                                     users={users || []}
                                     groupsList={groupsList || []} />
            )}
          </section>

          {/* Targeting */}
          <section className="border-t border-gray-100 pt-4">
            <h3 className="text-sm font-semibold text-ink mb-1">Who gets this task</h3>
            <p className="text-[11px] text-muted mb-3">
              Pick any combination — the system spawns one instance for the
              <strong> union</strong> of all matching users (de-duplicated).
            </p>

            <Field label="Groups">
              <div className="flex flex-wrap gap-1.5">
                {(groupsList || []).map(g => {
                  const checked = form.assigned_group_ids.includes(g.id)
                  return (
                    <button
                      key={g.id} type="button"
                      onClick={() => toggleGroup(g.id)}
                      className={`text-[11px] px-2 py-1 rounded border ${
                        checked ? 'bg-plum-100 border-plum-300 text-plum-700'
                                : 'bg-white border-gray-200 text-muted hover:border-plum-300'
                      }`}
                    >
                      {g.name} ({g.member_count})
                    </button>
                  )
                })}
              </div>
            </Field>

            <Field label="Specific users (in addition to groups)">
              <UserPicker
                users={users || []}
                selected={form.assigned_users}
                onChange={(next) => updateForm({ assigned_users: next })}
              />
            </Field>

            <Field label="Anyone with this permission">
              <select className="input text-sm font-mono"
                      value={form.assigned_permission}
                      onChange={e => updateForm({ assigned_permission: e.target.value })}>
                <option value="">— none —</option>
                {(catalog?.permissions || []).map(p => (
                  <option key={p.key} value={p.key}>{p.key} — {p.description}</option>
                ))}
              </select>
            </Field>

            {!isNew && (
              <div className="mt-3 bg-plum-50/40 border border-plum-100 rounded p-3">
                <button type="button" className="btn-secondary text-xs flex items-center gap-1"
                        onClick={() => preview.mutate()} disabled={preview.isPending}>
                  <Eye size={11} /> {preview.isPending ? 'Computing…' : 'Preview assignees'}
                </button>
                {previewResult && (
                  <div className="mt-2 text-[11px]">
                    <div className="text-muted">
                      <strong>{previewResult.count}</strong> user{previewResult.count === 1 ? '' : 's'} would receive this task today:
                    </div>
                    <div className="font-mono text-[10px] text-plum-700 mt-1">
                      {previewResult.assignees.length === 0
                        ? <em className="text-amber-600">— no one —</em>
                        : previewResult.assignees.join(', ')}
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>

          {save.isError && (
            <div className="text-danger text-xs">
              {save.error?.response?.data?.detail || 'error saving'}
            </div>
          )}

          <div className="sticky bottom-0 bg-white border-t border-border-subtle pt-3 -mx-6 px-6 flex items-center justify-between">
            <div>
              {!isNew && (
                <button
                  className="text-danger text-[12px] flex items-center gap-1 hover:underline"
                  onClick={() => {
                    if (confirm(`Delete template "${form.title}"? This will also remove all task instances generated from it.`)) {
                      remove.mutate()
                    }
                  }}
                  disabled={remove.isPending}
                >
                  <Trash2 size={12} /> Delete Template
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
              <button className="btn-primary text-sm"
                      onClick={() => save.mutate()}
                      disabled={!form.title.trim() || !form.escalate_to_email || save.isPending}>
                {save.isPending ? 'Saving…' : (isNew ? 'Create' : 'Save')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}


function TrainersAndTrainees({ templateId, users, groupsList }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data: trainersData, isLoading: trainersLoading } = useQuery({
    queryKey: ['training-trainers', templateId],
    queryFn: () => api.get('/training/trainers',
                            { params: { template_id: templateId } }).then(r => r.data),
  })
  const { data: certsData, isLoading: certsLoading } = useQuery({
    queryKey: ['training-certs', templateId],
    queryFn: () => api.get('/training/certifications',
                            { params: { template_id: templateId } }).then(r => r.data),
  })
  function invalidate() {
    qc.invalidateQueries({ queryKey: ['training-trainers', templateId] })
    qc.invalidateQueries({ queryKey: ['training-certs',    templateId] })
    qc.invalidateQueries({ queryKey: ['admin-templates'] })
  }

  // Display-name lookup → sort by first name, then last name, then email
  const userByEmail = new Map((users || []).map(u => [u.email, u]))
  function sortKeyFor(email) {
    const u = userByEmail.get(email)
    const dn = (u?.display_name || '').trim()
    // "First Last" → ["First","Last"]. Empty display_name falls back to email.
    if (!dn) return [email || '', '', email || '']
    const parts = dn.split(/\s+/)
    const first = parts[0] || ''
    const last  = parts.slice(1).join(' ')
    return [first.toLowerCase(), last.toLowerCase(), (email || '').toLowerCase()]
  }
  function cmpByFirstName(a, b) {
    const ka = sortKeyFor(a.user_email)
    const kb = sortKeyFor(b.user_email)
    for (let i = 0; i < ka.length; i++) {
      if (ka[i] < kb[i]) return -1
      if (ka[i] > kb[i]) return  1
    }
    return 0
  }

  const trainers = (trainersData?.trainers || [])
                     .filter(t => !t.revoked_at)
                     .sort(cmpByFirstName)
  // Show active + pending so admins can see who's waiting on acknowledgement
  const certs    = (certsData?.certifications || [])
                     .filter(c => c.status === 'active' || c.status === 'pending_trainee')
                     .sort(cmpByFirstName)

  const [addTrainerEmail, setAddTrainerEmail] = useState('')
  const [addTraineeEmail, setAddTraineeEmail] = useState('')
  const [bulkGroupId, setBulkGroupId]         = useState('')

  const addTrainer = useMutation({
    mutationFn: () => api.post('/training/trainers',
                                 { user_email: addTrainerEmail, template_id: templateId })
                        .then(r => r.data),
    onSuccess: () => { invalidate(); setAddTrainerEmail('') },
    onError: (e) => alert(e?.response?.data?.detail || 'Authorize failed'),
  })
  const revokeTrainer = useMutation({
    mutationFn: (email) => api.delete('/training/trainers',
                                       { data: { user_email: email, template_id: templateId } })
                            .then(r => r.data),
    onSuccess: invalidate,
    onError: (e) => alert(e?.response?.data?.detail || 'Revoke failed'),
  })
  const certify = useMutation({
    mutationFn: () => api.post('/training/certifications',
                                 { trainee_email: addTraineeEmail, template_id: templateId })
                        .then(r => r.data),
    onSuccess: () => { invalidate(); setAddTraineeEmail('') },
    onError: (e) => alert(e?.response?.data?.detail || 'Certify failed'),
  })
  const certifyGroup = useMutation({
    mutationFn: () => api.post('/training/certify-group',
                                 { group_id: bulkGroupId, template_id: templateId })
                        .then(r => r.data),
    onSuccess: (data) => {
      invalidate()
      const issued = data?.issued?.length ?? 0
      const skipped = data?.skipped?.length ?? 0
      alert(`Issued ${issued}, skipped ${skipped}.`)
      setBulkGroupId('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Bulk certify failed'),
  })
  const revokeCert = useMutation({
    mutationFn: (id) => api.delete(`/training/certifications/${id}`,
                                    { data: { reason: 'revoked from template editor' } })
                          .then(r => r.data),
    onSuccess: invalidate,
    onError: (e) => alert(e?.response?.data?.detail || 'Revoke failed'),
  })
  const forceAck = useMutation({
    mutationFn: (id) => api.post(`/training/certifications/${id}/force-acknowledge`)
                          .then(r => r.data),
    onSuccess: invalidate,
    onError: (e) => alert(e?.response?.data?.detail || 'Activation failed'),
  })

  // Build a quick map of available trainees (active users) and trainers
  const userOptions = (users || []).filter(u => u.is_active !== false)
  const trainerEmails = new Set(trainers.map(t => t.user_email))
  // certs already filtered to active + pending_trainee; both block re-add
  const certifiedEmails = new Set(certs.map(c => c.user_email))

  return (
    <div className="mt-4 border-t border-gray-100 pt-4 space-y-4">
      <div>
        <h4 className="text-[12px] font-semibold text-ink mb-1">Authorized Trainers</h4>
        <p className="text-[11px] text-muted mb-2">
          Users who can sign off that someone has been trained on this template.
        </p>
        {trainersLoading ? (
          <div className="text-[11px] text-muted italic">Loading…</div>
        ) : trainers.length === 0 ? (
          <div className="text-[11px] text-amber-700 italic">No authorized trainers yet.</div>
        ) : (
          <ul className="space-y-1 mb-2">
            {trainers.map(t => (
              <li key={t.id} className="flex items-center gap-2 text-[12px]">
                <span className="font-mono">{t.user_email}</span>
                <span className="text-[10px] text-muted">
                  authorized {fmt.date(t.authorized_at?.slice(0, 10))}
                  {t.authorized_by && ` by ${t.authorized_by.split('@')[0]}`}
                </span>
                <button type="button"
                         className="ml-auto text-[10px] text-danger hover:underline"
                         onClick={async () => {
                           if (await confirm({
                             title: 'Revoke trainer?',
                             message: `${t.user_email} will no longer be a trainer for this template.`,
                             confirmLabel: 'Revoke',
                           }))
                             revokeTrainer.mutate(t.user_email)
                         }}>
                  revoke
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-1">
          <select className="input text-[12px] flex-1"
                   value={addTrainerEmail}
                   onChange={e => setAddTrainerEmail(e.target.value)}>
            <option value="">— pick user to authorize as trainer —</option>
            {userOptions
              .filter(u => !trainerEmails.has(u.email))
              .map(u => (
                <option key={u.email} value={u.email}>
                  {u.display_name || u.email} ({u.email})
                </option>
              ))}
          </select>
          <button type="button"
                   className="btn-primary text-[11px]"
                   disabled={!addTrainerEmail || addTrainer.isPending}
                   onClick={() => addTrainer.mutate()}>
            + Add trainer
          </button>
        </div>
      </div>

      <div>
        <h4 className="text-[12px] font-semibold text-ink mb-1">Certified Trainees</h4>
        <p className="text-[11px] text-muted mb-2">
          Users who have been certified on this template. Only certified users
          receive instances when "Require training" is on.
        </p>
        {certsLoading ? (
          <div className="text-[11px] text-muted italic">Loading…</div>
        ) : certs.length === 0 ? (
          <div className="text-[11px] text-amber-700 italic">No active certifications.</div>
        ) : (
          <ul className="space-y-1 mb-2 max-h-48 overflow-y-auto">
            {certs.map(c => {
              const pending = c.status === 'pending_trainee'
              return (
              <li key={c.id} className={`flex items-center gap-2 text-[12px] rounded px-1 ${
                    pending ? 'bg-amber-50' : ''
                  }`}>
                <span className="font-mono">{c.user_email}</span>
                {pending ? (
                  <span className="text-[10px] uppercase tracking-wide bg-amber-200 text-amber-800 px-1 rounded"
                        title="Trainer signed; waiting on the trainee to log in and acknowledge">
                    awaiting trainee
                  </span>
                ) : (
                  <span className="text-[10px] uppercase tracking-wide bg-green-200 text-green-800 px-1 rounded">
                    active
                  </span>
                )}
                <span className="text-[10px] text-muted">
                  by {c.trainer_email?.split('@')[0] || '—'}
                  {c.trainer_signed_at && ` · ${fmt.date(c.trainer_signed_at.slice(0, 10))}`}
                  {c.expires_on && ` · expires ${fmt.date(c.expires_on)}`}
                </span>
                {pending && (
                  <button type="button"
                           className="ml-auto text-[10px] text-plum-700 hover:underline"
                           title="Mark this trainee certified on their behalf (admin override)"
                           onClick={async () => {
                             if (await confirm({
                               title: 'Activate certification on their behalf?',
                               message: `${c.user_email} won't need to log in to acknowledge.`,
                               confirmLabel: 'Activate',
                               danger: false,
                             }))
                               forceAck.mutate(c.id)
                           }}>
                    activate now
                  </button>
                )}
                <button type="button"
                         className={`text-[10px] text-danger hover:underline ${pending ? '' : 'ml-auto'}`}
                         onClick={async () => {
                           if (await confirm({
                             title: 'Revoke certification?',
                             message: `${c.user_email}'s certification will be revoked.`,
                             confirmLabel: 'Revoke',
                           }))
                             revokeCert.mutate(c.id)
                         }}>
                  revoke
                </button>
              </li>
              )
            })}
          </ul>
        )}
        <div className="flex items-center gap-1">
          <select className="input text-[12px] flex-1"
                   value={addTraineeEmail}
                   onChange={e => setAddTraineeEmail(e.target.value)}>
            <option value="">— pick user to certify —</option>
            {userOptions
              .filter(u => !certifiedEmails.has(u.email))
              .map(u => (
                <option key={u.email} value={u.email}>
                  {u.display_name || u.email} ({u.email})
                </option>
              ))}
          </select>
          <button type="button"
                   className="btn-primary text-[11px]"
                   disabled={!addTraineeEmail || certify.isPending}
                   onClick={() => certify.mutate()}>
            + Certify trainee
          </button>
        </div>

        {/* Bulk: certify an entire group */}
        <div className="flex items-center gap-1 mt-2">
          <select className="input text-[12px] flex-1"
                   value={bulkGroupId}
                   onChange={e => setBulkGroupId(e.target.value)}>
            <option value="">— bulk certify every member of group —</option>
            {(groupsList || []).map(g => (
              <option key={g.id} value={g.id}>{g.name} ({g.member_count} members)</option>
            ))}
          </select>
          <button type="button"
                   className="btn-secondary text-[11px]"
                   disabled={!bulkGroupId || certifyGroup.isPending}
                   onClick={() => certifyGroup.mutate()}>
            Certify group
          </button>
        </div>
      </div>
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <label className="text-[10px] uppercase tracking-wide text-gray-500 mb-1 block">{label}</label>
      {children}
    </div>
  )
}


function UserPicker({ users, selected, onChange }) {
  const [text, setText] = useState('')
  const lower = text.toLowerCase().trim()
  const candidates = users
    .filter(u => !selected.includes(u.email))
    .filter(u => !lower
                 || u.email.toLowerCase().includes(lower)
                 || (u.display_name || '').toLowerCase().includes(lower))
    .slice(0, 6)

  return (
    <div>
      <div className="flex flex-wrap gap-1 mb-1">
        {selected.map(em => (
          <span key={em}
                className="text-[10px] bg-plum-100 text-plum-700 px-1.5 py-0.5 rounded flex items-center gap-1">
            <span className="font-mono">{em.split('@')[0]}</span>
            <button onClick={() => onChange(selected.filter(s => s !== em))}
                    className="hover:text-red-600">
              <X size={10} />
            </button>
          </span>
        ))}
      </div>
      <input
        className="input text-[12px] py-1 w-full"
        placeholder="Type to search by name or email…"
        value={text}
        onChange={e => setText(e.target.value)}
      />
      {text && candidates.length > 0 && (
        <div className="border border-gray-200 rounded mt-1 bg-white max-h-44 overflow-y-auto">
          {candidates.map(u => (
            <button
              key={u.email} type="button"
              className="block w-full text-left px-2 py-1.5 text-[11px] hover:bg-plum-50"
              onClick={() => { onChange([...selected, u.email]); setText('') }}
            >
              <span className="font-medium">{u.display_name || u.email.split('@')[0]}</span>
              <span className="text-muted ml-2 font-mono">{u.email}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
