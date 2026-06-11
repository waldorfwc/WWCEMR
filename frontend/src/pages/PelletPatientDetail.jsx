import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, User, Plus, CheckCircle2, Circle, Edit3, Save, X,
  DollarSign, Calendar, Pill, Shield, Send, ExternalLink, Trash2,
  PackageOpen, AlertTriangle, History, MessageSquare, Clock, RotateCcw,
  Replace,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { MODULE, TIER } from '../routes.jsx'


const LOC_LABEL = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
}


// Active-visit selection: prefer the soonest upcoming non-billed/non-cancelled
// visit. A past visit stuck in status='inserted' (awaiting billing close-out)
// is NOT what staff actively work — the next scheduled visit is.
function pickActiveVisit(visits) {
  const open = (visits || []).filter(v => v.status !== 'billed' && v.status !== 'cancelled')
  if (open.length === 0) return null
  const today = new Date().toISOString().slice(0, 10)
  const future = open
    .filter(v => v.scheduled_date && v.scheduled_date >= today)
    .sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date))
  if (future.length) return future[0]
  return open
    .slice()
    .sort((a, b) => (b.scheduled_date || '').localeCompare(a.scheduled_date || ''))[0]
}


export default function PelletPatientDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const [editingPrereq, setEditingPrereq] = useState(null)  // 'mammo' | 'labs' | null
  const [creatingVisit, setCreatingVisit] = useState(false)

  const { data: p, isLoading } = useQuery({
    queryKey: ['pellet-patient', id],
    queryFn: () => api.get(`/pellets/patients/${id}`).then(r => r.data),
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (!p) return <div className="p-6 text-red-600">Patient not found.</div>

  const visits = p.visits || []
  const activeVisit = pickActiveVisit(visits)
  // Surface a ModMed deep link in the header: prefer the active visit's
  // appointment link, then the most recent visit, falling back to the
  // patient-level link (set via the xlsx roster import).
  const modmedLink = (activeVisit && activeVisit.modmed_link) ||
                       visits.find(v => v.modmed_link)?.modmed_link ||
                       p.modmed_link || null

  return (
    <div className="max-w-5xl mx-auto">
      <Link to="/pellets/patients" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> All patients
      </Link>

      {/* Header */}
      <div className="card mb-3">
        <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
          <div>
            <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <User size={20} className="text-plum-700" />
              {p.patient_name}
            </h1>
            <div className="text-[12px] text-gray-500">
              Chart #{p.chart_number}
              {p.patient_dob && <> · DOB {fmt.date(p.patient_dob)}</>}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {modmedLink && (
              <a href={modmedLink} target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-plum-700 hover:underline flex items-center gap-1 px-2 py-1 border border-plum-200 rounded bg-plum-50/40"
                  title="Open in ModMed (new tab)">
                <ExternalLink size={11}/> Open in ModMed
              </a>
            )}
            <TypeBadge patient={p} qc={qc} />
            <StatusBadge patient={p} qc={qc} />
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[12px]">
          <Field label="Phone">
            <InlineTextEditor patient={p} field="patient_phone" qc={qc} />
          </Field>
          <Field label="Email">
            <InlineTextEditor patient={p} field="patient_email" qc={qc} />
          </Field>
          <Field label="Insurance">
            <InlineTextEditor patient={p} field="primary_insurance" qc={qc} />
          </Field>
          <Field label="Recall cadence">
            <RecallEditor patient={p} qc={qc} />
          </Field>
        </div>
      </div>

      {/* Preferences row — pinned right under the patient header */}
      <PreferencesRow patient={p} qc={qc} />

      {/* History per type */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        <MammoHistoryCard patient={p} qc={qc}
                            onAdd={() => setEditingPrereq('mammo')} />
        <LabsHistoryCard patient={p} qc={qc}
                          onAdd={() => setEditingPrereq('labs')} />
      </div>

      {/* Pellet Dosing — proposed dose flow + confirmed history,
          including a way to manually add historical visits */}
      <PelletDosingCard visits={visits} activeVisit={activeVisit}
                          patient={p} qc={qc} />

      {/* Active visit milestones */}
      {activeVisit ? (
        <VisitCard visit={activeVisit} patient={p} qc={qc} />
      ) : (
        <div className="card mb-3 bg-amber-50/50 border border-amber-200">
          <div className="flex items-baseline justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-800">No Active Visit</h2>
              <p className="text-[12px] text-gray-600 mt-1">
                {visits.length === 0
                  ? 'This patient hasn\'t started a pellet visit yet.'
                  : 'Their previous visit is closed. Start a new booster or repeat visit when needed.'}
              </p>
            </div>
            <button className="btn-primary text-sm flex items-center gap-1"
                    onClick={() => setCreatingVisit(true)}>
              <Plus size={13}/> New visit
            </button>
          </div>
        </div>
      )}

      {/* Patient-level notes */}
      <PatientNotesCard patient={p} qc={qc} />

      {editingPrereq === 'mammo' && (
        <MammoDrawer patient={p} qc={qc} onClose={() => setEditingPrereq(null)} />
      )}
      {editingPrereq === 'labs' && (
        <LabsDrawer patient={p} qc={qc} onClose={() => setEditingPrereq(null)} />
      )}
      {creatingVisit && (
        <NewVisitDrawer patient={p} qc={qc} onClose={() => setCreatingVisit(false)} />
      )}
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-gray-500">{label}</div>
      <div>{children}</div>
    </div>
  )
}


// ── Inline editor for plain string patient fields ──

function InlineTextEditor({ patient, field, qc, type = "text" }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(patient[field] || '')
  const save = useMutation({
    mutationFn: () => api.patch(`/pellets/patients/${patient.id}`,
                                  { [field]: val.trim() || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  if (editing) {
    return (
      <div className="flex items-center gap-1">
        <input type={type} className="input text-[12px] flex-1 py-0.5"
                value={val} autoFocus
                onChange={e => setVal(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') save.mutate()
                  if (e.key === 'Escape') { setEditing(false); setVal(patient[field] || '') }
                }} />
        <button onClick={() => save.mutate()}
                 className="text-plum-700 hover:bg-plum-50 p-0.5 rounded">
          <Save size={12}/>
        </button>
        <button onClick={() => { setEditing(false); setVal(patient[field] || '') }}
                 className="text-gray-500 hover:bg-gray-100 p-0.5 rounded">
          <X size={12}/>
        </button>
      </div>
    )
  }
  return (
    <button className="hover:underline text-plum-700 text-left"
            onClick={() => setEditing(true)}
            title="Click to edit">
      {patient[field] || <span className="text-gray-400 italic">—</span>}
    </button>
  )
}


// ── Type + status badges (clickable to toggle) ──

function TypeBadge({ patient, qc }) {
  const [editing, setEditing] = useState(false)
  const save = useMutation({
    mutationFn: (val) => api.patch(`/pellets/patients/${patient.id}`,
                                      { patient_type: val }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  if (editing) {
    return (
      <select className="input text-[11px] py-0.5"
              value={patient.patient_type}
              onChange={e => save.mutate(e.target.value)}>
        <option value="new">new ($500)</option>
        <option value="established">established ($400)</option>
      </select>
    )
  }
  return (
    <button onClick={() => setEditing(true)}
             className={`text-[10px] uppercase tracking-wide px-2 py-1 rounded hover:ring-2 hover:ring-plum-200 ${
               patient.patient_type === 'new'
                 ? 'bg-blue-100 text-blue-700'
                 : 'bg-gray-100 text-gray-700'
             }`}>
      {patient.patient_type} patient
    </button>
  )
}


function StatusBadge({ patient, qc }) {
  const [editing, setEditing] = useState(false)
  const save = useMutation({
    mutationFn: (val) => api.patch(`/pellets/patients/${patient.id}`,
                                      { status: val }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  if (editing) {
    return (
      <select className="input text-[11px] py-0.5"
              value={patient.status}
              onChange={e => save.mutate(e.target.value)}>
        <option value="active">active</option>
        <option value="inactive">inactive</option>
        <option value="declined">declined</option>
      </select>
    )
  }
  return (
    <button onClick={() => setEditing(true)}
             className={`text-[10px] uppercase tracking-wide px-2 py-1 rounded hover:ring-2 hover:ring-plum-200 ${
               patient.status === 'active' ? 'bg-green-100 text-green-700'
                 : patient.status === 'inactive' ? 'bg-gray-100 text-gray-700'
                 : 'bg-red-100 text-red-700'
             }`}>
      {patient.status}
    </button>
  )
}


// ── Provider info card (latest mammo + preferred lab) ──

// ── Preferences row (top-of-page, patient-level) ─────────────────

const LAB_OPTIONS = ["Labs at WWC", "LabCorp-External", "Quest-External"]


function PreferencesRow({ patient, qc }) {
  const [editingFacility, setEditingFacility] = useState(false)
  const [editingLab, setEditingLab] = useState(false)

  const facName = patient.preferred_mammo_facility_name
  const facPhone = patient.preferred_mammo_facility_phone
  const facFax = patient.preferred_mammo_facility_fax
  const facAddress = patient.preferred_mammo_facility_address
  const hasFacility = facName || facPhone || facFax || facAddress

  const labName = patient.preferred_lab_name
  const labPhone = patient.preferred_lab_phone
  const labAddress = patient.preferred_lab_address
  const hasLab = labName || labPhone || labAddress

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
      {/* Preferred mammogram facility */}
      <div className="card !p-3">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold">
            Preferred mammogram facility
          </div>
          <button onClick={() => setEditingFacility(true)}
                   className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
            <Edit3 size={11}/> Edit
          </button>
        </div>
        {hasFacility ? (
          <div className="text-[11px] text-gray-700 space-y-0.5">
            {facName && <div className="text-[13px] font-medium">{facName}</div>}
            {facPhone && <div className="font-mono">phone {facPhone}</div>}
            {facFax && <div className="font-mono text-gray-500">fax {facFax}</div>}
            {facAddress && <div className="text-gray-600 whitespace-pre-wrap">{facAddress}</div>}
          </div>
        ) : (
          <div className="text-[12px] text-gray-400 italic">Not set — click Edit to choose one.</div>
        )}
      </div>

      {/* Preferred lab — 3-option dropdown */}
      <div className="card !p-3">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold">
            Preferred lab
          </div>
          <button onClick={() => setEditingLab(true)}
                   className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
            <Edit3 size={11}/> Edit
          </button>
        </div>
        {hasLab ? (
          <div className="text-[11px] text-gray-700 space-y-0.5">
            {labName && <div className="text-[13px] font-medium">{labName}</div>}
            {labPhone && <div className="font-mono">{labPhone}</div>}
            {labAddress && <div className="text-gray-600 whitespace-pre-wrap">{labAddress}</div>}
          </div>
        ) : (
          <div className="text-[12px] text-gray-400 italic">Not set — click Edit to choose one.</div>
        )}
      </div>

      {editingFacility && (
        <MammoFacilityPicker patient={patient} qc={qc}
                               onClose={() => setEditingFacility(false)} />
      )}
      {editingLab && (
        <PreferredLabPicker patient={patient} qc={qc}
                              onClose={() => setEditingLab(false)} />
      )}
    </div>
  )
}


function PreferredLabPicker({ patient, qc, onClose }) {
  const [name, setName] = useState(patient.preferred_lab_name || '')
  const [phone, setPhone] = useState(patient.preferred_lab_phone || '')
  const [address, setAddress] = useState(patient.preferred_lab_address || '')

  const save = useMutation({
    mutationFn: () => api.patch(`/pellets/patients/${patient.id}`, {
      preferred_lab_name:    name || null,
      preferred_lab_phone:   phone.trim() || null,
      preferred_lab_address: address.trim() || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <SimpleDrawer title="Preferred lab" onClose={onClose}>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Lab *</label>
        <select className="input text-sm w-full" value={name}
                onChange={e => setName(e.target.value)}>
          <option value="">— pick a lab —</option>
          {LAB_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        <div className="text-[10px] text-gray-500 mt-1">
          <strong>Labs at WWC</strong> = drawn in-house. <strong>LabCorp / Quest — External</strong>{' '}
          = sent to the patient's preferred outside lab.
        </div>
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Phone (optional)</label>
        <input className="input text-sm w-full font-mono"
                placeholder="Branch contact, if needed"
                value={phone} onChange={e => setPhone(e.target.value)} />
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Address (optional)</label>
        <textarea className="input text-[12px] w-full" rows={2}
                   value={address} onChange={e => setAddress(e.target.value)} />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={!name}
                     label="Save preferred lab" />
    </SimpleDrawer>
  )
}


function MammoFacilityPicker({ patient, qc, onClose }) {
  // Catalog of nearby facilities (curated list within ~15 mi of Waldorf)
  const { data: facilities = [] } = useQuery({
    queryKey: ['pellet-mammo-facilities'],
    queryFn: () => api.get('/pellets/mammo-facilities').then(r => r.data),
    staleTime: 60_000 * 10,
  })

  // Detect the currently-selected facility from the curated list by name
  const initialMatch = facilities.find(
    f => f.name === patient.preferred_mammo_facility_name
  )
  const [pickerVal, setPickerVal] = useState(initialMatch ? initialMatch.id : 'custom')
  const [name, setName] = useState(patient.preferred_mammo_facility_name || '')
  const [phone, setPhone] = useState(patient.preferred_mammo_facility_phone || '')
  const [fax, setFax] = useState(patient.preferred_mammo_facility_fax || '')
  const [address, setAddress] = useState(patient.preferred_mammo_facility_address || '')

  function selectCatalogEntry(id) {
    setPickerVal(id)
    if (id === '' || id === 'custom') return
    const f = facilities.find(x => x.id === id)
    if (!f) return
    setName(f.name)
    setPhone(f.phone || '')
    setFax(f.fax || '')
    setAddress(f.address || '')
  }
  function clearFields() {
    setPickerVal('custom')
    setName(''); setPhone(''); setFax(''); setAddress('')
  }

  const save = useMutation({
    mutationFn: () => api.patch(`/pellets/patients/${patient.id}`, {
      preferred_mammo_facility_name:    name.trim() || null,
      preferred_mammo_facility_phone:   phone.trim() || null,
      preferred_mammo_facility_fax:     fax.trim() || null,
      preferred_mammo_facility_address: address.trim() || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <SimpleDrawer title="Preferred mammogram facility" onClose={onClose}>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">
          Pick from list (within ~15 miles of Waldorf)
        </label>
        <select className="input text-sm w-full" value={pickerVal}
                onChange={e => selectCatalogEntry(e.target.value)}>
          <option value="custom">— Custom (enter manually below) —</option>
          {facilities.map(f => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
        </select>
        <div className="text-[10px] text-gray-500 mt-1">
          Picking one auto-fills the fields below; you can still edit them
          before saving (e.g. correct a phone extension or add a suite #).
        </div>
      </div>

      <div className="border-t border-gray-100 pt-2 mt-2">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-[10px] uppercase tracking-wide text-gray-500 font-semibold">
            Facility details
          </div>
          <button type="button" onClick={clearFields}
                   className="text-[10px] text-gray-500 hover:text-red-700 hover:underline">
            Clear
          </button>
        </div>
        <div className="space-y-2">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Name</label>
            <input className="input text-sm w-full"
                    placeholder="e.g. Charles Regional Imaging"
                    value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Phone</label>
              <input className="input text-sm w-full font-mono"
                      placeholder="(301) 555-1212"
                      value={phone} onChange={e => setPhone(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Fax</label>
              <input className="input text-sm w-full font-mono"
                      placeholder="(301) 555-1213"
                      value={fax} onChange={e => setFax(e.target.value)} />
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Address</label>
            <textarea className="input text-[12px] w-full" rows={2}
                       value={address}
                       onChange={e => setAddress(e.target.value)} />
          </div>
        </div>
      </div>

      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     label="Save preferred facility" />
    </SimpleDrawer>
  )
}


// ── Mammogram history ──

function MammoHistoryCard({ patient, qc, onAdd }) {
  const mammos = patient.mammos || []
  const del = useMutation({
    mutationFn: (id) => api.delete(`/pellets/patients/${patient.id}/mammos/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  return (
    <div className={`border rounded p-3 ${
      patient.mammo_verified ? 'border-green-200 bg-green-50/40' : 'border-amber-200 bg-amber-50/40'
    }`}>
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <strong className="text-[13px]">Mammogram history</strong>
          <div className="text-[10px] text-gray-500">BI-RADS 1 or 2 required</div>
        </div>
        <button onClick={onAdd}
                 className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
          <Plus size={11}/> Add entry
        </button>
      </div>
      {mammos.length === 0 ? (
        <div className="text-[12px] text-amber-700">No mammograms on file.</div>
      ) : (
        <ul className="space-y-1 text-[12px]">
          {mammos.map((m, idx) => (
            <li key={m.id} className="flex items-baseline justify-between gap-2 py-0.5 border-b border-gray-100 last:border-0">
              <div className="flex-1">
                <span className="font-medium">{fmt.date(m.mammo_date)}</span>
                <span className="ml-2">{m.result}</span>
                {idx === 0 && (
                  <span className="ml-1 text-[9px] bg-green-100 text-green-700 px-1 rounded">latest</span>
                )}
                {m.facility_name && (
                  <div className="text-[10px] text-gray-600">
                    {m.facility_name}
                    {m.facility_phone && <span className="ml-1 font-mono text-gray-500">· {m.facility_phone}</span>}
                  </div>
                )}
                {m.notes && (
                  <div className="text-[10px] text-gray-500 italic">{m.notes}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {m.verified_by && (
                  <span className="text-[10px] text-gray-400">{m.verified_by.split('@')[0]}</span>
                )}
                <button onClick={() => { if (window.confirm('Delete this mammo entry?')) del.mutate(m.id) }}
                         className="text-red-600 hover:bg-red-50 p-0.5 rounded">
                  <Trash2 size={10}/>
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


// ── Labs history ──

function LabsHistoryCard({ patient, qc, onAdd }) {
  const labs = patient.labs || []
  const notRequired = !!patient.labs_not_required
  const del = useMutation({
    mutationFn: (id) => api.delete(`/pellets/patients/${patient.id}/labs/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })
  const setNotRequired = useMutation({
    mutationFn: (val) => api.patch(`/pellets/patients/${patient.id}`,
                                    { labs_not_required: val }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })

  return (
    <div className={`border rounded p-3 ${
      (patient.labs_verified || notRequired) ? 'border-green-200 bg-green-50/40' : 'border-amber-200 bg-amber-50/40'
    }`}>
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <strong className="text-[13px]">Labs history</strong>
          <div className="text-[10px] text-gray-500">FSH · TSH · Estradiol</div>
        </div>
        <button onClick={onAdd}
                 className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
          <Plus size={11}/> Add entry
        </button>
      </div>
      <label className="flex items-center gap-1.5 text-[11px] text-gray-700 mb-2 cursor-pointer select-none">
        <input type="checkbox"
               checked={notRequired}
               disabled={setNotRequired.isPending}
               onChange={e => setNotRequired.mutate(e.target.checked)} />
        Labs not required for this patient (e.g. testosterone-only)
      </label>
      {notRequired ? (
        <div className="text-[12px] text-green-700">Labs marked not required.</div>
      ) : labs.length === 0 ? (
        <div className="text-[12px] text-amber-700">No labs on file.</div>
      ) : (
        <ul className="space-y-1 text-[12px]">
          {labs.map((l, idx) => (
            <li key={l.id} className="flex items-baseline justify-between gap-2 py-0.5 border-b border-gray-100 last:border-0">
              <div className="flex-1">
                <span className="font-medium">{fmt.date(l.labs_date)}</span>
                <span className="ml-2 text-[11px] text-gray-700">
                  FSH {l.fsh || '?'} <span className="text-gray-400">mIU/mL</span>
                  {' · '}TSH {l.tsh || '?'} <span className="text-gray-400">µIU/mL</span>
                  {' · '}E2 {l.estradiol || '?'} <span className="text-gray-400">pg/mL</span>
                </span>
                {idx === 0 && (
                  <span className="ml-1 text-[9px] bg-green-100 text-green-700 px-1 rounded">latest</span>
                )}
                {l.notes && (
                  <div className="text-[10px] text-gray-500 italic">{l.notes}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {l.verified_by && (
                  <span className="text-[10px] text-gray-400">{l.verified_by.split('@')[0]}</span>
                )}
                <button onClick={() => { if (window.confirm('Delete this lab entry?')) del.mutate(l.id) }}
                         className="text-red-600 hover:bg-red-50 p-0.5 rounded">
                  <Trash2 size={10}/>
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


// ── Patient notes ──

function PatientNotesCard({ patient, qc }) {
  const [body, setBody] = useState('')
  const notes = patient.notes || []
  const add = useMutation({
    mutationFn: () => api.post(`/pellets/patients/${patient.id}/notes`,
                                  { body }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      setBody('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const del = useMutation({
    mutationFn: (id) => api.delete(`/pellets/patients/${patient.id}/notes/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] }),
  })

  return (
    <div className="card mb-3">
      <h2 className="text-sm font-semibold mb-2 text-gray-800 flex items-center gap-1">
        <MessageSquare size={14} className="text-plum-700"/>
        Patient notes ({notes.length})
      </h2>
      <div className="space-y-2 mb-2 max-h-72 overflow-y-auto">
        {notes.length === 0 && (
          <div className="text-[12px] text-gray-400 italic">No notes yet.</div>
        )}
        {notes.map(n => (
          <div key={n.id} className="border-l-2 border-plum-200 pl-2 py-0.5 flex items-baseline justify-between">
            <div className="flex-1">
              <div className="text-[10px] text-gray-500">
                {n.author?.split('@')[0]} · {fmt.date(n.created_at.slice(0, 10))}{' '}
                {fmt.time(n.created_at)}
              </div>
              <div className="text-[12px] text-gray-800 whitespace-pre-wrap">{n.body}</div>
            </div>
            <button onClick={() => { if (window.confirm('Delete this note?')) del.mutate(n.id) }}
                     className="text-red-600 hover:bg-red-50 p-0.5 rounded shrink-0">
              <Trash2 size={10}/>
            </button>
          </div>
        ))}
      </div>
      <textarea className="input text-[12px] w-full" rows={2}
                placeholder="Add a note about this patient…"
                value={body} onChange={e => setBody(e.target.value)} />
      <button className="btn-secondary text-[11px] mt-1"
              onClick={() => add.mutate()}
              disabled={!body.trim() || add.isPending}>
        {add.isPending ? 'Saving…' : 'Add note'}
      </button>
    </div>
  )
}


function RecallEditor({ patient, qc }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(patient.recall_interval_months || 4)
  const save = useMutation({
    mutationFn: () => api.patch(`/pellets/patients/${patient.id}`,
                                  { recall_interval_months: Number(val) }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  if (editing) {
    return (
      <div className="flex items-center gap-1">
        <input type="number" min="1" max="24"
               className="input text-[12px] w-12 py-0.5"
               value={val} onChange={e => setVal(e.target.value)} autoFocus />
        <span className="text-[11px] text-gray-500">months</span>
        <button onClick={() => save.mutate()}
                 className="text-plum-700 hover:bg-plum-50 p-0.5 rounded"
                 title="Save">
          <Save size={12}/>
        </button>
        <button onClick={() => { setEditing(false); setVal(patient.recall_interval_months || 4) }}
                 className="text-gray-500 hover:bg-gray-100 p-0.5 rounded">
          <X size={12}/>
        </button>
      </div>
    )
  }
  return (
    <button className="hover:underline text-plum-700"
            onClick={() => setEditing(true)}
            title="Click to edit">
      every {patient.recall_interval_months || 4} months
    </button>
  )
}


function MammoDrawer({ patient, qc, onClose }) {
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [result, setResult] = useState('BI-RADS 1')
  // Pre-fill from the patient's preferred mammo facility (single source
  // of truth); fall back to whatever the last entry used.
  const lastMammo = (patient.mammos || [])[0]
  const [facilityName, setFacilityName] = useState(
    patient.preferred_mammo_facility_name || lastMammo?.facility_name || ''
  )
  const [facilityPhone, setFacilityPhone] = useState(
    patient.preferred_mammo_facility_phone || lastMammo?.facility_phone || ''
  )
  const [facilityAddress, setFacilityAddress] = useState(
    patient.preferred_mammo_facility_address || lastMammo?.facility_address || ''
  )
  const [notes, setNotes] = useState('')

  const save = useMutation({
    mutationFn: () => api.post(`/pellets/patients/${patient.id}/verify-mammo`, {
      mammo_date: date,
      mammo_result: result,
      facility_name:    facilityName.trim() || null,
      facility_phone:   facilityPhone.trim() || null,
      facility_address: facilityAddress.trim() || null,
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <SimpleDrawer title="Add mammogram entry" onClose={onClose}>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Date *</label>
        <input type="date" className="input text-sm w-full" required
               value={date} onChange={e => setDate(e.target.value)} />
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Result *</label>
        <select className="input text-sm w-full" value={result}
                onChange={e => setResult(e.target.value)}>
          <option value="BI-RADS 1">BI-RADS 1 (negative)</option>
          <option value="BI-RADS 2">BI-RADS 2 (benign)</option>
          <option value="BI-RADS 0">BI-RADS 0 (incomplete)</option>
          <option value="BI-RADS 3">BI-RADS 3 (probably benign)</option>
          <option value="BI-RADS 4">BI-RADS 4 (suspicious)</option>
          <option value="BI-RADS 5">BI-RADS 5 (highly suggestive of malignancy)</option>
          <option value="Not Required - Testosterone Only">Not Required — Testosterone Only</option>
        </select>
      </div>
      <div className="border-t border-gray-100 pt-2">
        <div className="text-[10px] uppercase text-gray-500 font-semibold mb-1">
          Imaging facility (where the mammo was done)
        </div>
        <div className="space-y-2">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Facility name</label>
            <input className="input text-sm w-full"
                   placeholder="e.g. Charles Regional Imaging"
                   value={facilityName}
                   onChange={e => setFacilityName(e.target.value)} />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Phone</label>
            <input className="input text-sm w-full font-mono"
                   placeholder="(301) 555-1212"
                   value={facilityPhone}
                   onChange={e => setFacilityPhone(e.target.value)} />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Address</label>
            <textarea className="input text-[12px] w-full" rows={2}
                       value={facilityAddress}
                       onChange={e => setFacilityAddress(e.target.value)} />
          </div>
        </div>
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                  value={notes} onChange={e => setNotes(e.target.value)}
                  placeholder="Optional — lateralization, finding details, etc." />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={!date || !result} />
    </SimpleDrawer>
  )
}


function LabsDrawer({ patient, qc, onClose }) {
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [fsh, setFsh] = useState('')
  const [tsh, setTsh] = useState('')
  const [e2, setE2] = useState('')
  const [notes, setNotes] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/pellets/patients/${patient.id}/verify-labs`,
                                 { labs_date: date, labs_fsh: fsh,
                                   labs_tsh: tsh, labs_estradiol: e2,
                                   notes: notes || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <SimpleDrawer title="Add labs entry" onClose={onClose}>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Date *</label>
        <input type="date" className="input text-sm w-full" required
               value={date} onChange={e => setDate(e.target.value)} />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">FSH</label>
          <div className="relative">
            <input className="input text-sm w-full pr-12" value={fsh}
                    onChange={e => setFsh(e.target.value)} placeholder="e.g. 45" />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-gray-400 pointer-events-none">mIU/mL</span>
          </div>
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">TSH</label>
          <div className="relative">
            <input className="input text-sm w-full pr-14" value={tsh}
                    onChange={e => setTsh(e.target.value)} placeholder="e.g. 2.4" />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-gray-400 pointer-events-none">µIU/mL</span>
          </div>
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Estradiol</label>
          <div className="relative">
            <input className="input text-sm w-full pr-10" value={e2}
                    onChange={e => setE2(e.target.value)} placeholder="e.g. 35" />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-gray-400 pointer-events-none">pg/mL</span>
          </div>
        </div>
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                  value={notes} onChange={e => setNotes(e.target.value)} />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={!date} />
    </SimpleDrawer>
  )
}


function NewVisitDrawer({ patient, qc, onClose }) {
  const navigate = useNavigate()
  const [visitKind, setVisitKind] = useState('initial')
  const [scheduledDate, setScheduledDate] = useState('')
  const [location, setLocation] = useState('white_plains')
  const [notes, setNotes] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/pellets/visits', {
      patient_id: patient.id, visit_kind: visitKind,
      scheduled_date: scheduledDate || null,
      location, notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  return (
    <SimpleDrawer title="Start new pellet visit" onClose={onClose}>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Visit kind *</label>
          <select className="input text-sm w-full" value={visitKind}
                   onChange={e => setVisitKind(e.target.value)}>
            <option value="initial">Initial insertion</option>
            <option value="booster">Booster (extra dose)</option>
            <option value="repeat">Repeat (next cycle)</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
          <select className="input text-sm w-full" value={location}
                   onChange={e => setLocation(e.target.value)}>
            {Object.entries(LOC_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Scheduled date (optional)</label>
        <input type="date" className="input text-sm w-full"
               value={scheduledDate}
               onChange={e => setScheduledDate(e.target.value)} />
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                  value={notes} onChange={e => setNotes(e.target.value)} />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => create.mutate()}
                     saving={create.isPending}
                     disabled={false}
                     label="Create visit" />
    </SimpleDrawer>
  )
}


function VisitCard({ visit, patient, qc }) {
  const [bagOpen, setBagOpen] = useState(false)
  const [insertionOpen, setInsertionOpen] = useState(false)
  const [addMidOpen, setAddMidOpen] = useState(false)
  const [confirmInsertionOpen, setConfirmInsertionOpen] = useState(false)
  const [disposeDose, setDisposeDose] = useState(null)   // a dose obj
  const [rescheduleOpen, setRescheduleOpen] = useState(false)
  const [cancelOpen, setCancelOpen]         = useState(false)

  const doses = visit.doses || []
  const hasPlanned = doses.some(d => d.status === 'planned')
  const hasPulled = doses.some(d => ['pulled', 'added'].includes(d.status))
  const allDone = doses.length > 0 && doses.every(
    d => ['inserted', 'returned', 'disposed', 'reduced'].includes(d.status)
  )
  const milestonesByKind = Object.fromEntries(
    (visit.milestones || []).map(m => [m.kind, m])
  )

  return (
    <div className="card mb-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-1">
            <Pill size={14} className="text-plum-700"/>
            Active visit · {visit.visit_kind}
          </h2>
          <div className="text-[12px] text-gray-500">
            Status: <strong>{visit.status.replace(/_/g, ' ')}</strong>
            {visit.scheduled_date && <> · scheduled {fmt.date(visit.scheduled_date)}</>}
            {visit.location && <> · {LOC_LABEL[visit.location] || visit.location}</>}
            {visit.price_amount != null && <> · ${visit.price_amount}</>}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button className="btn-secondary text-[11px] flex items-center gap-1 py-1 px-2"
                  onClick={() => setRescheduleOpen(true)}
                  title="Reschedule this visit">
            <Calendar size={11}/> Reschedule
          </button>
          <button className="text-[11px] flex items-center gap-1 py-1 px-2 rounded border border-red-200 bg-white hover:bg-red-50 text-red-700"
                  onClick={() => setCancelOpen(true)}
                  title="Cancel this visit">
            <X size={11}/> Cancel
          </button>
        </div>
      </div>

      {/* Dose card — visible block */}
      <DoseCardBlock visit={visit}
                       onFillBag={() => setBagOpen(true)}
                       onAddMid={() => setAddMidOpen(true)}
                       onDispose={(d) => setDisposeDose(d)} />

      {/* Milestones */}
      <ul className="space-y-1.5 mt-4">
        {(visit.milestones || []).map(m => (
          <MilestoneRow key={m.id} visit={visit} milestone={m} qc={qc} />
        ))}
      </ul>

      {/* Payment workflow (Klara → ModMed) */}
      {milestonesByKind.payment_collected
       && milestonesByKind.payment_collected.status === 'pending' && (
        <PaymentBox visit={visit} qc={qc} />
      )}

      {/* Confirm-what-was-inserted (per-line) and Reschedule/Cancel outcome */}
      {(hasPlanned || hasPulled) && !['billed','cancelled'].includes(visit.status) && (
        <div className="mt-3 border-t border-gray-100 pt-3 flex flex-wrap gap-2">
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setConfirmInsertionOpen(true)}>
            <CheckCircle2 size={12}/> Confirm What Was Inserted…
          </button>
          {hasPulled && visit.status === 'in_progress' && (
            <button className="text-sm flex items-center gap-1 px-3 py-1.5 rounded border border-gray-300 bg-white hover:bg-gray-50 text-gray-700"
                    onClick={() => setInsertionOpen(true)}
                    title="Reschedule or cancel without confirming each line">
              <RotateCcw size={12}/> Reschedule / Cancel
            </button>
          )}
        </div>
      )}

      {/* Bill close-out when inserted */}
      {visit.status === 'inserted' && !visit.claim_number && (
        <BillBox visit={visit} qc={qc} />
      )}
      {visit.claim_number && (
        <div className="mt-3 text-[12px] text-green-700">
          ✓ Billed under claim #<span className="font-mono">{visit.claim_number}</span>
          {visit.billed_at && <> on {fmt.date(visit.billed_at.slice(0, 10))}</>}
        </div>
      )}

      {/* Reversible status — step one stage back, audited */}
      <RevertControl visit={visit} patient={patient} qc={qc} />

      {/* Drawers */}
      {bagOpen && (
        <BagFillDrawer visit={visit} qc={qc} onClose={() => setBagOpen(false)} />
      )}
      {insertionOpen && (
        <InsertionOutcomeDrawer visit={visit} qc={qc}
                                  onClose={() => setInsertionOpen(false)} />
      )}
      {confirmInsertionOpen && (
        <ConfirmInsertionDrawer visit={visit} qc={qc}
                                  onClose={() => setConfirmInsertionOpen(false)} />
      )}
      {addMidOpen && (
        <MidProcedureAddDrawer visit={visit} qc={qc}
                                onClose={() => setAddMidOpen(false)} />
      )}
      {disposeDose && (
        <MidProcedureDisposeDrawer visit={visit} dose={disposeDose} qc={qc}
                                     onClose={() => setDisposeDose(null)} />
      )}
      {rescheduleOpen && (
        <RescheduleVisitDrawer visit={visit} qc={qc}
                                onClose={() => setRescheduleOpen(false)} />
      )}
      {cancelOpen && (
        <CancelVisitDrawer visit={visit} qc={qc}
                            onClose={() => setCancelOpen(false)} />
      )}
    </div>
  )
}


function RevertControl({ visit, patient, qc }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [showHistory, setShowHistory] = useState(false)

  const bagged = (visit.milestones || []).some(m => m.kind === 'bagged' && m.status === 'done')
  const target =
    visit.status === 'billed'                          ? { verb: 'Un-bill',   to: 'inserted' }  :
    visit.status === 'inserted'                        ? { verb: 'Un-insert', to: 'bagged' }    :
    (visit.status === 'in_progress' && bagged)         ? { verb: 'Un-bag',    to: 'scheduled' } :
    null

  const revert = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/revert`,
                                { reason: reason.trim() }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      qc.invalidateQueries({ queryKey: ['pellet-visit-transitions', visit.id] })
      setOpen(false); setReason('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Revert failed'),
  })

  const { data: history } = useQuery({
    queryKey: ['pellet-visit-transitions', visit.id],
    queryFn: () => api.get(`/pellets/visits/${visit.id}/transitions`).then(r => r.data),
    enabled: showHistory,
  })

  return (
    <div className="mt-3 border-t border-gray-100 pt-3">
      <div className="flex items-center gap-3">
        {target && !open && (
          <button className="text-[11px] flex items-center gap-1 text-amber-700 hover:underline"
                  onClick={() => setOpen(true)}
                  title={`Step this visit back: ${target.verb} → ${target.to}`}>
            <RotateCcw size={11}/> {target.verb} (→ {target.to})
          </button>
        )}
        <button className="text-[11px] text-gray-500 hover:underline flex items-center gap-1"
                onClick={() => setShowHistory(s => !s)}>
          <History size={11}/> {showHistory ? 'Hide history' : 'Status history'}
        </button>
      </div>

      {open && target && (
        <div className="mt-2 p-2 rounded border border-amber-200 bg-amber-50/50">
          <div className="text-[11px] text-gray-700 mb-1">
            {target.verb}: <strong>{visit.status.replace(/_/g, ' ')}</strong> → <strong>{target.to}</strong>.
            {' '}A reason is required (logged with your name).
          </div>
          <textarea className="input text-[12px] w-full" rows={2}
                    placeholder="Reason for reverting…"
                    value={reason} onChange={e => setReason(e.target.value)} />
          <div className="flex gap-2 justify-end mt-1">
            <button className="btn-secondary text-[11px]"
                    onClick={() => { setOpen(false); setReason('') }}>Cancel</button>
            <button className="text-[11px] px-2 py-1 rounded text-white bg-amber-700 hover:bg-amber-800 disabled:opacity-50"
                    disabled={!reason.trim() || revert.isPending}
                    onClick={() => revert.mutate()}>
              {revert.isPending ? 'Reverting…' : `Confirm ${target.verb}`}
            </button>
          </div>
        </div>
      )}

      {showHistory && (
        <ul className="mt-2 space-y-1">
          {(history || []).length === 0 ? (
            <li className="text-[11px] text-gray-400 italic">No status changes recorded.</li>
          ) : history.map(h => (
            <li key={h.id} className="text-[11px] text-gray-600">
              <span className="font-mono">{h.before} → {h.after}</span>
              {' · '}{(h.actor || '').split('@')[0]}
              {h.at && <> · {fmt.date(h.at.slice(0, 10))}</>}
              {h.reason && <> · "{h.reason}"</>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


function RescheduleVisitDrawer({ visit, qc, onClose }) {
  const [date, setDate]     = useState(visit.scheduled_date || '')
  const [reason, setReason] = useState('')
  const [busy, setBusy]     = useState(false)
  const [err, setErr]       = useState(null)

  async function submit() {
    if (!date) { setErr('Pick a new date'); return }
    setBusy(true); setErr(null)
    try {
      await api.post(`/pellets/visits/${visit.id}/reschedule`, {
        new_date: date,
        reason: reason.trim() || null,
      })
      qc.invalidateQueries({ queryKey: ['pellet-patient'] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      onClose()
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <DrawerShell title="Reschedule visit" onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div>
          <div className="text-[10px] uppercase text-gray-500 mb-1">New date</div>
          <input type="date" className="input text-sm w-full"
                  value={date} onChange={e => setDate(e.target.value)} />
          <div className="text-[10px] text-gray-500 mt-1">
            Current: {visit.scheduled_date ? fmt.date(visit.scheduled_date) : '(unscheduled)'}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500 mb-1">Reason (optional)</div>
          <textarea className="input text-sm w-full" rows={2}
                    placeholder="e.g. patient requested later date, weather, etc."
                    value={reason} onChange={e => setReason(e.target.value)} />
        </div>
        {err && <div className="text-xs text-red-600">{err}</div>}
        <div className="flex gap-2 justify-end">
          <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-xs" onClick={submit} disabled={busy || !date}>
            {busy ? 'Saving…' : 'Reschedule'}
          </button>
        </div>
      </div>
    </DrawerShell>
  )
}


function CancelVisitDrawer({ visit, qc, onClose }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy]     = useState(false)
  const [err, setErr]       = useState(null)

  const pulledDoses = (visit.doses || []).filter(d => d.status === 'pulled' || d.status === 'added')

  async function submit() {
    if (!reason.trim()) { setErr('Reason required'); return }
    setBusy(true); setErr(null)
    try {
      await api.post(`/pellets/visits/${visit.id}/cancel`, { reason: reason.trim() })
      qc.invalidateQueries({ queryKey: ['pellet-patient'] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      onClose()
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <DrawerShell title="Cancel visit" onClose={onClose}>
      <div className="space-y-3 text-sm">
        {pulledDoses.length > 0 && (
          <div className="text-xs bg-amber-50 border border-amber-200 rounded p-2 text-amber-800">
            <strong>{pulledDoses.length}</strong> pulled dose{pulledDoses.length === 1 ? '' : 's'} will
            be returned to stock at {LOC_LABEL[visit.location] || visit.location || '—'}.
          </div>
        )}
        <div>
          <div className="text-[10px] uppercase text-gray-500 mb-1">Reason *</div>
          <textarea className="input text-sm w-full" rows={3}
                    placeholder="Why is this visit being cancelled?"
                    value={reason} onChange={e => setReason(e.target.value)}
                    autoFocus />
        </div>
        {err && <div className="text-xs text-red-600">{err}</div>}
        <div className="flex gap-2 justify-end">
          <button className="btn-secondary text-xs" onClick={onClose}>Keep Visit</button>
          <button className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                  onClick={submit} disabled={busy || !reason.trim()}>
            {busy ? 'Cancelling…' : 'Cancel visit'}
          </button>
        </div>
      </div>
    </DrawerShell>
  )
}


function DrawerShell({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-4 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[15px]">{title}</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={16}/></button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  )
}


// ── Dose card block (visible) ────────────────────────────────────────

const DOSE_STATUS_TONES = {
  planned:  'bg-gray-100 text-gray-700',
  pulled:   'bg-blue-100 text-blue-700',
  added:    'bg-violet-100 text-violet-700',
  inserted: 'bg-green-100 text-green-700',
  reduced:  'bg-amber-100 text-amber-700',
  returned: 'bg-gray-100 text-gray-500',
  disposed: 'bg-red-100 text-red-700',
}


function DoseCardBlock({ visit, onFillBag, onAddMid, onDispose }) {
  const doses = visit.doses || []
  const planned = doses.filter(d => d.status === 'planned')
  const hasProposed = doses.some(d => ['planned', 'pulled'].includes(d.status))
  const showAddMid = visit.status === 'in_progress' &&
                       doses.some(d => ['pulled', 'added'].includes(d.status))
  const [swapDose, setSwapDose] = useState(null)
  const [correctDose, setCorrectDose] = useState(null)   // retroactive identify-lot
  const [editProposedOpen, setEditProposedOpen] = useState(false)
  const { tier } = useCurrentUser()
  const canCorrectLot = tier(MODULE.PELLETS, TIER.MANAGE)

  return (
    <div className="border border-gray-200 rounded p-3 bg-gray-50/50">
      <div className="flex items-baseline justify-between mb-2 flex-wrap gap-2">
        <h3 className="text-[12px] font-semibold text-gray-800 uppercase tracking-wide">
          Dose card ({doses.length})
        </h3>
        <div className="flex items-center gap-2">
          {hasProposed && (
            <button className="text-[11px] flex items-center gap-1 px-2 py-1 rounded border border-plum-300 bg-white text-plum-700 hover:bg-plum-50"
                    onClick={() => setEditProposedOpen(true)}
                    title="Edit the proposed dose: change combination, quantity, or lots before bagging">
              <Edit3 size={11}/> Edit proposed dose
            </button>
          )}
          {planned.length > 0 && (
            <button className="btn-primary text-[11px] flex items-center gap-1"
                    onClick={onFillBag}>
              <PackageOpen size={11}/> Fill bag ({planned.length})
            </button>
          )}
          {showAddMid && (
            <button className="text-[11px] text-violet-700 hover:underline flex items-center gap-1"
                    onClick={onAddMid}>
              <Plus size={11}/> Add mid-procedure
            </button>
          )}
        </div>
      </div>
      {doses.length === 0 ? (
        <div className="text-[12px] text-gray-500 italic">
          No doses on this visit yet. Use <strong>Set dose</strong> in the
          Pellet Dosing card below to build the dose card.
        </div>
      ) : (
        <ul className="divide-y divide-gray-100 text-[12px]">
          {doses.map(d => {
            const isSwappable = ['planned', 'pulled'].includes(d.status)
            return (
              <li key={d.id} className="py-1.5 flex items-baseline justify-between gap-2">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-medium">{d.quantity}×</span>
                  <span>{d.dose_label}</span>
                  {d.is_controlled && (
                    <span className="text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
                  )}
                  {d.qualgen_lot && (
                    <span className="text-[10px] text-gray-500 font-mono">lot {d.qualgen_lot}</span>
                  )}
                  {d.lot_expiration_date && (
                    <span className="text-[10px] text-gray-400">
                      exp {fmt.date(d.lot_expiration_date)}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`text-[9px] uppercase px-1.5 py-0.5 rounded ${DOSE_STATUS_TONES[d.status] || ''}`}>
                    {d.status}
                  </span>
                  {isSwappable && (
                    <button className="text-plum-700 hover:bg-plum-50 p-0.5 rounded"
                            title="Swap lot for this proposed dose"
                            onClick={() => setSwapDose(d)}>
                      <Replace size={11}/>
                    </button>
                  )}
                  {!isSwappable && canCorrectLot && (
                    <button className="text-amber-700 hover:bg-amber-50 p-0.5 rounded"
                            title="Correct lot retroactively (manager only) — for fixing a lot that wasn't captured at pre-bag time"
                            onClick={() => setCorrectDose(d)}>
                      <Edit3 size={11}/>
                    </button>
                  )}
                  {['pulled', 'added'].includes(d.status) && (
                    <button className="text-red-600 hover:bg-red-50 p-0.5 rounded"
                            title="Mid-procedure disposal (dropped / broken)"
                            onClick={() => onDispose(d)}>
                      <Trash2 size={11}/>
                    </button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {swapDose && (
        <LotSwapDrawer
          visit={visit}
          dose={swapDose}
          onClose={() => setSwapDose(null)}
        />
      )}
      {correctDose && (
        <CorrectLotDrawer
          visit={visit}
          dose={correctDose}
          onClose={() => setCorrectDose(null)}
        />
      )}
      {editProposedOpen && (
        <EditProposedDoseDrawer
          visit={visit}
          onClose={() => setEditProposedOpen(false)} />
      )}
    </div>
  )
}


function EditProposedDoseDrawer({ visit, onClose }) {
  // Edit the proposed-dose card holistically: change dose-type combination,
  // quantities, and lots — before the bag is filled. Submits the full new
  // card via PUT /pellets/visits/{id}/dose-card (already auto-returns the
  // prior reservation and pulls fresh per line).
  const qc = useQueryClient()
  const initial = (visit.doses || [])
    .filter(d => ['planned', 'pulled'].includes(d.status))
    .sort((a, b) => (a.position || 0) - (b.position || 0))
    .map(d => ({
      dose_type_id: d.dose_type_id,
      dose_label:   d.dose_label,
      quantity:     d.quantity,
      lot_id:       d.lot_id || '',
      qualgen_lot:  d.qualgen_lot || '',
      lot_expiration_date: d.lot_expiration_date || null,
    }))
  const [rows, setRows] = useState(
    initial.length > 0
      ? initial
      : [{ dose_type_id: '', quantity: 1, lot_id: '' }]
  )
  const [error, setError] = useState(null)

  const { data: doseTypes } = useQuery({
    queryKey: ['pellet-dose-types-active'],
    queryFn: () => api.get('/pellets/dose-types').then(r => r.data),
    staleTime: 5 * 60_000,
  })
  const dtList = Array.isArray(doseTypes)
    ? doseTypes
    : (doseTypes?.dose_types || doseTypes?.types || [])
  const dtOptions = dtList.filter(t => t.is_active !== false)

  function updateRow(i, patch) {
    setRows(prev => prev.map((r, j) => j === i ? { ...r, ...patch } : r))
  }
  function addRow() {
    setRows(prev => [...prev, { dose_type_id: '', quantity: 1, lot_id: '' }])
  }
  function removeRow(i) {
    setRows(prev => prev.filter((_, j) => j !== i))
  }

  const submit = useMutation({
    mutationFn: () => api.put(`/pellets/visits/${visit.id}/dose-card`, {
      doses: rows
        .filter(r => r.dose_type_id && Number(r.quantity) > 0)
        .map(r => ({
          dose_type_id: r.dose_type_id,
          quantity:     Number(r.quantity),
          ...(r.lot_id ? { lot_id: r.lot_id } : {}),
        })),
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      onClose()
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Save failed'))
    },
  })

  const validRows = rows.filter(r => r.dose_type_id && Number(r.quantity) > 0)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-3 flex items-center justify-between z-10">
          <h2 className="text-[15px] font-semibold text-gray-900">
            Edit proposed dose
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-3">
          <p className="text-[12px] text-gray-600">
            Replace the proposed dose for this visit. Old planned/pulled
            doses return to inventory and the new combination is pulled.
            Bagging and insertion are separate steps; this only affects what's
            <em> proposed</em>.
          </p>

          <ul className="space-y-2">
            {rows.map((r, i) => (
              <li key={i} className="bg-gray-50 border border-gray-200 rounded p-2 space-y-1.5">
                <div className="grid grid-cols-[1fr_80px_24px] gap-2">
                  <select className="input text-[12px]"
                          value={r.dose_type_id}
                          onChange={e => updateRow(i, { dose_type_id: e.target.value, lot_id: '',
                                                          qualgen_lot: '', lot_expiration_date: null })}>
                    <option value="">— select dose type —</option>
                    {dtOptions.map(t => (
                      <option key={t.id} value={t.id}>{t.label}</option>
                    ))}
                  </select>
                  <input type="number" min="1" className="input text-[12px] font-mono"
                         value={r.quantity}
                         onChange={e => updateRow(i, { quantity: e.target.value })} />
                  <button onClick={() => removeRow(i)}
                          className="text-red-600 hover:bg-red-50 rounded p-1"
                          title="Remove this row">
                    <Trash2 size={12} />
                  </button>
                </div>
                {(r.qualgen_lot || r.lot_expiration_date) && (
                  <div className="text-[11px] text-gray-600 flex items-center gap-2 px-0.5">
                    {r.qualgen_lot && (
                      <span className="font-mono">lot {r.qualgen_lot}</span>
                    )}
                    {r.lot_expiration_date && (
                      <span className="text-gray-500">exp {fmt.date(r.lot_expiration_date)}</span>
                    )}
                  </div>
                )}
                {r.dose_type_id && (
                  <LotPickerForType
                    doseTypeId={r.dose_type_id}
                    location={visit.location}
                    minQty={Number(r.quantity) || 1}
                    selected={r.lot_id}
                    onSelect={(id) => updateRow(i, { lot_id: id || '' })} />
                )}
              </li>
            ))}
          </ul>

          <button onClick={addRow}
                  className="text-[11px] text-plum-700 hover:underline flex items-center gap-1">
            <Plus size={11}/> Add another dose
          </button>

          {error && (
            <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onClose} className="btn-secondary text-sm">Cancel</button>
            <button onClick={() => submit.mutate()}
                    disabled={submit.isPending || validRows.length === 0}
                    className="btn-primary text-sm">
              {submit.isPending ? 'Saving…' : `Save (${validRows.length} dose${validRows.length === 1 ? '' : 's'})`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function LotSwapDrawer({ visit, dose, onClose }) {
  const qc = useQueryClient()
  const [error, setError] = useState(null)
  const { data, isLoading } = useQuery({
    queryKey: ['lot-options', dose.dose_type_id, visit.location],
    queryFn: () => api.get('/pellets/lots', {
      params: {
        dose_type_id: dose.dose_type_id,
        location:     visit.location,
        in_stock_only: true,
      },
    }).then(r => r.data),
  })

  const swap = useMutation({
    mutationFn: (lot_id) =>
      api.patch(`/pellets/visits/${visit.id}/doses/${dose.id}`, { lot_id })
         .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      onClose()
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Swap failed'))
    },
  })

  const lots = (data?.lots || [])
    .filter(l => (l.balances?.[visit.location] || 0) >= dose.quantity)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-3 flex items-center justify-between">
          <h2 className="text-[15px] font-semibold text-gray-900">
            Swap lot · {dose.quantity}× {dose.dose_label}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-3">
          <div className="text-[12px] text-gray-600">
            Current lot: <strong className="font-mono">{dose.qualgen_lot || '—'}</strong>
            {dose.lot_expiration_date && <> · exp {fmt.date(dose.lot_expiration_date)}</>}
            {' · '}at {visit.location}
          </div>
          {isLoading ? (
            <div className="text-[12px] text-gray-500 italic">Loading lots…</div>
          ) : lots.length === 0 ? (
            <div className="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
              No other lots have ≥{dose.quantity} dose(s) of {dose.dose_label} at {visit.location}.
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {lots.map(l => {
                const isCurrent = String(l.id) === String(dose.lot_id)
                const stockHere = l.balances?.[visit.location] || 0
                return (
                  <li key={l.id} className="py-2 flex items-center justify-between gap-2">
                    <div className="text-[12px]">
                      <div className="font-mono">{l.qualgen_lot_number}</div>
                      <div className="text-[11px] text-gray-500">
                        exp {l.expiration_date ? fmt.date(l.expiration_date) : '—'}
                        {' · '}{stockHere} at {visit.location}
                      </div>
                    </div>
                    <button className="btn-primary text-[11px] disabled:opacity-50"
                            disabled={isCurrent || swap.isPending}
                            onClick={() => swap.mutate(l.id)}>
                      {isCurrent ? 'Current' : 'Use this lot'}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {error && (
            <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function CorrectLotDrawer({ visit, dose, onClose }) {
  // Retroactive lot identification for terminal-status doses (inserted /
  // added / reduced / returned / disposed). Uses the manager-only
  // POST /pellets/visits/{vid}/doses/{did}/identify-lot endpoint which
  // rebalances stock (returns to the prior lot, debits the new one) and
  // records a 3-row audit trail. The reason string is required.
  const qc = useQueryClient()
  const [reason, setReason] = useState('')
  const [error, setError] = useState(null)
  const { data, isLoading } = useQuery({
    queryKey: ['correct-lot-options', dose.dose_type_id, visit.location],
    queryFn: () => api.get('/pellets/lots', {
      params: { dose_type_id: dose.dose_type_id, location: visit.location },
    }).then(r => r.data),
  })

  const identify = useMutation({
    mutationFn: (lot_id) =>
      api.post(`/pellets/visits/${visit.id}/doses/${dose.id}/identify-lot`,
                { lot_id, reason: reason.trim() })
         .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-audit'] })
      onClose()
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Correction failed'))
    },
  })

  const lots = data?.lots || []
  const reasonOk = reason.trim().length >= 6

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-3 flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-semibold text-gray-900">
              Correct lot · {dose.quantity}× {dose.dose_label}
            </h2>
            <div className="text-[10px] text-amber-700 uppercase tracking-wide">
              Manager-only · retroactive
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-3">
          <div className="text-[12px] bg-amber-50 border border-amber-200 rounded p-2 text-amber-900">
            This dose is already <strong className="font-mono">{dose.status}</strong>.
            Use this when the lot wasn't captured at pre-bag time or
            the wrong lot was recorded. Stock is rebalanced (returned
            to the previously-debited lot, debited from the new one)
            and every change is audited.
          </div>
          <div className="text-[12px] text-gray-600">
            Current lot:{' '}
            <strong className="font-mono">{dose.qualgen_lot || 'none recorded'}</strong>
            {dose.lot_expiration_date && <> · exp {fmt.date(dose.lot_expiration_date)}</>}
            {' · '}at {visit.location}
          </div>
          <div>
            <label className="block text-[11px] text-gray-700 mb-1">
              Reason <span className="text-red-600">*</span>{' '}
              <span className="text-gray-400">(min 6 chars)</span>
            </label>
            <textarea
              rows={2}
              className="w-full text-[12px] border border-gray-300 rounded px-2 py-1"
              placeholder="e.g. lot identified from paper bag manifest"
              value={reason}
              onChange={e => setReason(e.target.value)}
            />
          </div>
          {isLoading ? (
            <div className="text-[12px] text-gray-500 italic">Loading lots…</div>
          ) : lots.length === 0 ? (
            <div className="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
              No lots of {dose.dose_label} exist for {visit.location}.
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {lots.map(l => {
                const isCurrent = String(l.id) === String(dose.lot_id)
                const stockHere = l.balances?.[visit.location] || 0
                return (
                  <li key={l.id} className="py-2 flex items-center justify-between gap-2">
                    <div className="text-[12px]">
                      <div className="font-mono">{l.qualgen_lot_number}</div>
                      <div className="text-[11px] text-gray-500">
                        exp {l.expiration_date ? fmt.date(l.expiration_date) : '—'}
                        {' · '}{stockHere} at {visit.location}
                      </div>
                    </div>
                    <button
                      className="text-[11px] px-2 py-1 rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-50 disabled:opacity-50"
                      disabled={isCurrent || !reasonOk || identify.isPending}
                      onClick={() => identify.mutate(l.id)}
                      title={isCurrent ? 'Already identified with this lot'
                        : !reasonOk ? 'Provide a reason (min 6 chars) first'
                        : 'Identify this lot — debits its stock, returns any prior debit'}>
                      {isCurrent ? 'Current' : 'Identify this lot'}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {error && (
            <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function MilestoneRow({ visit, milestone, qc }) {
  const isDone = milestone.status !== 'pending'
  const advance = useMutation({
    mutationFn: (status) => api.post(
      `/pellets/visits/${visit.id}/milestones/${milestone.id}/advance`,
      { status }
    ).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Advance failed'),
  })

  return (
    <li className={`flex items-baseline gap-2 text-[12px] ${isDone ? 'opacity-60' : ''}`}>
      {isDone ? (
        <CheckCircle2 size={14} className="text-green-600 shrink-0 mt-0.5" />
      ) : (
        <Circle size={14} className="text-gray-300 shrink-0 mt-0.5" />
      )}
      <div className="flex-1">
        <div className="flex items-baseline gap-2">
          <span className={isDone ? 'line-through text-gray-500' : 'font-medium'}>
            {milestone.title}
          </span>
          {milestone.completed_at && (
            <span className="text-[10px] text-gray-400">
              {fmt.date(milestone.completed_at.slice(0, 10))}
              {milestone.completed_by && ` · ${milestone.completed_by.split('@')[0]}`}
            </span>
          )}
        </div>
        {milestone.notes && (
          <div className="text-[11px] text-gray-500 italic">{milestone.notes}</div>
        )}
      </div>
      <div className="flex gap-1">
        {milestone.status === 'pending' && (
          <button className="text-[10px] text-plum-700 hover:underline"
                  onClick={() => advance.mutate('done')}>
            Mark done
          </button>
        )}
        {milestone.status === 'pending' && (
          <button className="text-[10px] text-gray-400 hover:text-plum-700"
                  onClick={() => advance.mutate('skipped')}>
            Skip
          </button>
        )}
        {milestone.status !== 'pending' && (
          <button className="text-[10px] text-gray-400 hover:text-plum-700"
                  onClick={() => advance.mutate('pending')}>
            Reopen
          </button>
        )}
      </div>
    </li>
  )
}


function BillBox({ visit, qc }) {
  const [claim, setClaim] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/bill`,
                                 { claim_number: claim }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <div className="mt-3 border-t border-gray-100 pt-3 flex items-center gap-2">
      <DollarSign size={14} className="text-plum-700" />
      <input className="input text-sm flex-1 font-mono"
              placeholder="ModMed claim #"
              value={claim} onChange={e => setClaim(e.target.value)} />
      <button className="btn-primary text-[12px]"
              onClick={() => save.mutate()}
              disabled={!claim.trim() || save.isPending}>
        Save & close visit
      </button>
    </div>
  )
}


// ── Pellet Dosing — Proposed area (Set dose) + Confirmed history ──

function PelletDosingCard({ visits, activeVisit, patient, qc }) {
  const [settingDose, setSettingDose] = useState(false)
  const [addingHistorical, setAddingHistorical] = useState(false)
  const [editingHistorical, setEditingHistorical] = useState(null)
  const all = visits || []

  // Proposed = any visit with at least one planned/pulled dose line.
  // Confirmed (history) = visits with inserted_at OR is_historical OR a
  // dose line in a confirmed status.
  const isProposed = (v) =>
    !v.is_historical &&
    !['cancelled', 'billed'].includes(v.status) &&
    (v.doses || []).some(d => ['planned', 'pulled'].includes(d.status))
  const isConfirmedHistory = (v) =>
    v.is_historical ||
    !!v.inserted_at ||
    (v.doses || []).some(d => ['inserted', 'added', 'reduced', 'returned', 'disposed'].includes(d.status))

  const proposed = all.filter(isProposed)
  const history = all.filter(isConfirmedHistory).sort((a, b) => {
    const aDate = a.inserted_at || a.scheduled_date || a.created_at || ''
    const bDate = b.inserted_at || b.scheduled_date || b.created_at || ''
    return bDate.localeCompare(aDate)
  })

  return (
    <div className="card mb-3">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-1">
          <Pill size={14} className="text-plum-700"/>
          Pellet dosing
        </h2>
      </div>

      {/* ─── Proposed dose section ─── */}
      <div className="border border-blue-100 bg-blue-50/30 rounded p-2 mb-3">
        <div className="flex items-baseline justify-between mb-2">
          <h3 className="text-[12px] font-semibold text-blue-800 uppercase tracking-wide">
            Proposed dose
          </h3>
          <button className="btn-primary text-[11px] flex items-center gap-1"
                   onClick={() => setSettingDose(true)}>
            <Pill size={11}/> Set dose
          </button>
        </div>
        {proposed.length === 0 ? (
          <div className="text-[12px] text-gray-500 italic">
            No proposed dose. Click <strong>Set dose</strong> to build the dose card for
            the next appointment (or create one).
          </div>
        ) : (
          <ol className="space-y-2.5 text-[12px]">
            {proposed.map(v => (
              <VisitDoseRow key={v.id} visit={v} patient={patient} qc={qc}
                              isActive={v.id === activeVisit?.id}
                              proposedOnly />
            ))}
          </ol>
        )}
      </div>

      {/* ─── Dosing history section (confirmed visits) ─── */}
      <div className="border border-gray-200 rounded p-2">
        <div className="flex items-baseline justify-between mb-2">
          <h3 className="text-[12px] font-semibold text-gray-800 uppercase tracking-wide flex items-center gap-1">
            <History size={11}/> Dosing history
            <span className="text-[10px] font-normal text-gray-500">
              ({history.length} visit{history.length === 1 ? '' : 's'})
            </span>
          </h3>
          <button className="btn-secondary text-[11px] flex items-center gap-1"
                   onClick={() => setAddingHistorical(true)}>
            <Plus size={11}/> Add historical entry
          </button>
        </div>
        {history.length === 0 ? (
          <div className="text-[12px] text-gray-400 italic">
            No confirmed visits yet. Use <strong>Add historical entry</strong> to record
            past appointments — these don't affect inventory.
          </div>
        ) : (
          <ol className="space-y-2.5 text-[12px]">
            {history.map(v => (
              v.is_historical
                ? <HistoricalVisitRow key={v.id} visit={v} patient={patient} qc={qc}
                                          onEdit={() => setEditingHistorical(v)} />
                : <VisitDoseRow key={v.id} visit={v} patient={patient} qc={qc}
                                    isActive={v.id === activeVisit?.id}
                                    confirmedOnly />
            ))}
          </ol>
        )}
        <div className="text-[10px] text-gray-400 italic mt-2">
          Historical entries are for backfill only — they record the appointment date
          (and optional dose notes) and do not touch inventory or chain-of-custody.
        </div>
      </div>

      {settingDose && (
        <SetDoseDrawer patient={patient} visits={all}
                          qc={qc} onClose={() => setSettingDose(false)} />
      )}
      {addingHistorical && (
        <HistoricalVisitDrawer patient={patient} qc={qc}
                                   onClose={() => setAddingHistorical(false)} />
      )}
      {editingHistorical && (
        <HistoricalVisitDrawer patient={patient} qc={qc}
                                   editing={editingHistorical}
                                   onClose={() => setEditingHistorical(null)} />
      )}
    </div>
  )
}


function HistoricalVisitRow({ visit, patient, qc, onEdit }) {
  const v = visit
  const del = useMutation({
    mutationFn: () => api.delete(`/pellets/visits/${v.id}/historical`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })
  return (
    <li className="border-l-2 border-gray-300 pl-3 py-1">
      <div className="flex items-baseline justify-between flex-wrap gap-1">
        <div>
          <strong>{v.visit_kind}</strong>
          {' · '}
          {v.scheduled_date ? fmt.date(v.scheduled_date) : '(no date)'}
          <span className="ml-1 text-[10px] bg-gray-100 text-gray-600 px-1 rounded uppercase">
            historical
          </span>
          {v.location && <span className="ml-1 text-[10px] text-gray-500">{LOC_LABEL[v.location] || v.location}</span>}
        </div>
        <div className="flex items-center gap-2">
          <button className="text-plum-700 hover:underline text-[10px] flex items-center gap-0.5"
                   onClick={onEdit}>
            <Edit3 size={10}/> Edit
          </button>
          <button className="text-red-600 hover:underline text-[10px] flex items-center gap-0.5"
                   onClick={() => {
                     if (confirm('Delete this historical visit entry? No inventory impact.')) {
                       del.mutate()
                     }
                   }}
                   disabled={del.isPending}>
            <X size={10}/> Delete
          </button>
        </div>
      </div>
      {v.outcome_notes && (
        <div className="text-[11px] text-gray-600 mt-0.5">{v.outcome_notes}</div>
      )}
      {v.provider && (
        <div className="text-[10px] text-gray-500">Provider: {v.provider}</div>
      )}
    </li>
  )
}


function HistoricalVisitDrawer({ patient, qc, onClose, editing }) {
  const isEdit = !!editing
  const [date, setDate]         = useState(editing?.scheduled_date || '')
  const [kind, setKind]         = useState(editing?.visit_kind || 'repeat')
  const [location, setLocation] = useState(editing?.location || '')
  const [provider, setProvider] = useState(editing?.provider || '')
  const [doseSummary, setDoseSummary] = useState(editing?.outcome_notes || '')
  const [notes, setNotes]       = useState(editing?.notes || '')

  const save = useMutation({
    mutationFn: () => {
      const body = {
        scheduled_date: date,
        visit_kind:     kind,
        location:       location || null,
        provider:       provider.trim() || null,
        outcome_notes:  doseSummary.trim() || null,
        notes:          notes.trim() || null,
      }
      return isEdit
        ? api.patch(`/pellets/visits/${editing.id}/historical`, body).then(r => r.data)
        : api.post(`/pellets/patients/${patient.id}/historical-visits`, body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const canSave = !!date

  return (
    <SimpleDrawer title={isEdit ? 'Edit historical visit' : 'Add historical visit'}
                    onClose={onClose}>
      <div className="text-[12px] text-gray-600 bg-gray-50 border border-gray-200 rounded p-2">
        Manually record a past pellet visit (e.g. imported from an old chart). This
        entry <strong>does not affect inventory</strong> — no stock changes, no chain
        of custody, no milestones. Just date + free-form dose notes.
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Appointment date *</label>
          <input type="date" className="input text-sm w-full" value={date}
                  onChange={e => setDate(e.target.value)} />
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Visit kind</label>
          <select className="input text-sm w-full" value={kind}
                   onChange={e => setKind(e.target.value)}>
            <option value="initial">initial</option>
            <option value="booster">booster</option>
            <option value="repeat">repeat</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
          <select className="input text-sm w-full" value={location}
                   onChange={e => setLocation(e.target.value)}>
            <option value="">—</option>
            <option value="white_plains">White Plains</option>
            <option value="brandywine">Brandywine</option>
            <option value="arlington">Arlington</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Provider</label>
          <input className="input text-sm w-full" value={provider}
                  onChange={e => setProvider(e.target.value)} />
        </div>
      </div>

      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Dose summary (optional)</label>
        <textarea className="input text-[12px] w-full" rows={2}
                   placeholder="e.g. E 25mg + T 100mg (2× 12.5mg estradiol + 1× 100mg testosterone)"
                   value={doseSummary}
                   onChange={e => setDoseSummary(e.target.value)} />
        <div className="text-[10px] text-gray-400 mt-0.5">
          Free-form. Fill in as much or as little detail as you have from the old record.
        </div>
      </div>

      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                   value={notes} onChange={e => setNotes(e.target.value)} />
      </div>

      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={!canSave}
                     label={isEdit ? 'Save Changes' : 'Add Historical Entry'} />
    </SimpleDrawer>
  )
}


function VisitDoseRow({ visit, patient, isActive, qc, proposedOnly, confirmedOnly }) {
  const v = visit
  const allDoses = v.doses || []
  // Confirmed = after-procedure (provider has signed off). Proposed =
  // before-procedure (still editable without manager privilege).
  const proposed = allDoses.filter(d => ['planned', 'pulled'].includes(d.status))
  const inserted = allDoses.filter(d => d.status === 'inserted')
  const added    = allDoses.filter(d => d.status === 'added')
  const returned = allDoses.filter(d => ['returned', 'reduced'].includes(d.status))
  const disposed = allDoses.filter(d => d.status === 'disposed')

  // Section-filtering: parent decides whether to render only Proposed
  // or only Confirmed groups so the same row component can serve both
  // sections of the new Pellet Dosing card.
  const showProposed = !confirmedOnly
  const showConfirmed = !proposedOnly
  const isConfirmedVisit = ['inserted', 'billed'].includes(v.status)

  const del = useMutation({
    mutationFn: (doseId) => api.delete(`/pellets/visits/${v.id}/doses/${doseId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function tone(status) {
    return status === 'billed' ? 'text-green-700'
         : status === 'cancelled' ? 'text-gray-500'
         : status === 'inserted' ? 'text-blue-700'
         : 'text-amber-700'
  }

  return (
    <li className={`border-l-2 pl-3 py-1 ${
      isActive ? 'border-plum-500' : 'border-gray-200'
    }`}>
      <div className="flex items-baseline justify-between flex-wrap gap-1">
        <div>
          <strong>{v.visit_kind}</strong>
          {v.inserted_at && <> · {fmt.date(v.inserted_at.slice(0, 10))}</>}
          {!v.inserted_at && v.scheduled_date && <> · scheduled {fmt.date(v.scheduled_date)}</>}
          {' · '}
          <span className={tone(v.status)}>{v.status.replace(/_/g, ' ')}</span>
          {v.outcome && v.outcome !== 'perfect' && <> · <em>{v.outcome}</em></>}
          {isActive && (
            <span className="ml-1 text-[10px] bg-plum-100 text-plum-700 px-1 rounded">active</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {v.modmed_link && (
            <a href={v.modmed_link} target="_blank" rel="noopener noreferrer"
                className="text-plum-700 hover:underline text-[10px] flex items-center gap-0.5"
                title="Open this appointment in ModMed">
              <ExternalLink size={10}/> ModMed
            </a>
          )}
          {v.claim_number && (
            <span className="font-mono text-gray-500 text-[10px]">claim #{v.claim_number}</span>
          )}
        </div>
      </div>
      {allDoses.length === 0 && (
        <div className="text-[10px] text-gray-400 italic mt-0.5">No doses recorded.</div>
      )}
      {/* Proposed (before-procedure) — anyone with pellet:work can delete */}
      {showProposed && (
        <DoseLineGroup label="Proposed"  color="text-blue-700"  doses={proposed} onDel={del.mutate} canDel />
      )}
      {/* Confirmed (after-procedure) — manager-only edits */}
      {showConfirmed && (
        <>
          <DoseLineGroup label="Confirmed" color="text-green-700" doses={inserted} onDel={del.mutate}
                           canDel managerOnly />
          <DoseLineGroup label="Confirmed (added mid-procedure)" color="text-violet-700"
                           doses={added} onDel={del.mutate} canDel managerOnly />
          <DoseLineGroup label="Confirmed disposal"  color="text-red-700"
                           doses={disposed} onDel={del.mutate} canDel managerOnly />
          <DoseLineGroup label="Returned to stock" color="text-gray-500"
                           doses={returned} onDel={del.mutate} canDel managerOnly />
        </>
      )}
    </li>
  )
}


function DoseLineGroup({ label, color, doses, onDel, canDel, managerOnly }) {
  // `'user:manage'` used to be a fallback for super-admin; tier() now
  // short-circuits on super-admin so the OR is redundant.
  const { tier } = useCurrentUser()
  const isMgr = tier(MODULE.PELLETS, TIER.MANAGE)
  const canActuallyDel = canDel && (!managerOnly || isMgr)
  if (doses.length === 0) return null
  return (
    <div className="text-[11px] mt-0.5 flex items-baseline gap-1 flex-wrap">
      <strong className={color}>{label}:</strong>
      {doses.map((d, i) => (
        <span key={d.id} className="inline-flex items-center gap-0.5 bg-gray-50 border border-gray-200 rounded px-1.5 py-0">
          {d.quantity}× {d.dose_label}
          {d.is_controlled && (
            <span className="ml-0.5 text-[8px] bg-amber-100 text-amber-700 px-0.5 rounded">SCH III</span>
          )}
          {d.qualgen_lot && (
            <span className="ml-0.5 text-[9px] text-gray-500 font-mono">lot {d.qualgen_lot}</span>
          )}
          {d.lot_expiration_date && (
            <span className="ml-0.5 text-[9px] text-gray-400">
              exp {fmt.date(d.lot_expiration_date)}
            </span>
          )}
          {canActuallyDel && onDel && (
            <button onClick={() => {
                       const msg = managerOnly
                         ? `Delete this CONFIRMED dose entry? (Manager override — logged in audit.)`
                         : `Delete this proposed dose? Stock will be returned automatically.`
                       if (window.confirm(msg)) onDel(d.id)
                     }}
                     className="ml-0.5 text-gray-400 hover:text-red-600"
                     title={managerOnly ? 'Delete confirmed dose (manager only)' : 'Delete proposed dose'}>
              <X size={9}/>
            </button>
          )}
          {managerOnly && !isMgr && (
            <span className="ml-0.5 text-[8px] text-gray-400" title="Manager-only edit">
              🔒
            </span>
          )}
        </span>
      ))}
    </div>
  )
}


// ── Set dose (with inventory check + alternatives + visit linking) ──

const LEAD_DAYS = 7   // 7-day pre-bag window before insertion


function SetDoseDrawer({ patient, visits, qc, onClose }) {
  const today = new Date(); today.setHours(0, 0, 0, 0)

  // 1. Pull prior dose for established patients
  const { data: prior } = useQuery({
    queryKey: ['pellet-prior-dose', patient.id],
    queryFn: () => api.get(`/pellets/patients/${patient.id}/prior-dose`).then(r => r.data),
  })

  // 2. Target mg per hormone — defaults to prior dose for established
  const [estradiolMg, setEstradiolMg] = useState('')
  const [testosteroneMg, setTestosteroneMg] = useState('')
  const [autoFilled, setAutoFilled] = useState(false)

  useEffect(() => {
    if (autoFilled || !prior) return
    if (patient.patient_type !== 'established') {
      setAutoFilled(true); return
    }
    if (prior.estradiol_mg > 0 || prior.testosterone_mg > 0) {
      setEstradiolMg(prior.estradiol_mg > 0 ? String(prior.estradiol_mg) : '')
      setTestosteroneMg(prior.testosterone_mg > 0 ? String(prior.testosterone_mg) : '')
    }
    setAutoFilled(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prior])

  // 3. Visit selection
  const futureVisits = (visits || []).filter(v => {
    if (['billed', 'cancelled', 'inserted'].includes(v.status)) return false
    if (!v.scheduled_date) return false
    return v.scheduled_date >= toISODate(today)
  }).sort((a, b) => (a.scheduled_date || '').localeCompare(b.scheduled_date || ''))

  const [visitChoice, setVisitChoice] = useState(() => futureVisits[0]?.id || 'create')
  const [newApptDate, setNewApptDate] = useState(() => {
    // Default to today + 14 days so there's time to pre-bag
    const d = new Date(); d.setDate(d.getDate() + 14)
    return toISODate(d)
  })
  const [newApptLocation, setNewApptLocation] = useState('white_plains')

  // 4. Inventory check — query the suggestion endpoint on every input change
  const targetEst = Number(estradiolMg) || 0
  const targetT = Number(testosteroneMg) || 0
  const visitLocation = visitChoice === 'create'
    ? newApptLocation
    : (futureVisits.find(v => v.id === visitChoice)?.location || 'white_plains')

  const suggest = useQuery({
    queryKey: ['pellet-dose-suggest', targetEst, targetT, visitLocation],
    queryFn: () => api.post('/pellets/dosing/suggest', {
      estradiol_mg:    targetEst,
      testosterone_mg: targetT,
      location:        visitLocation,
    }).then(r => r.data),
    enabled: targetEst > 0 || targetT > 0,
  })

  // 5. Pick the chosen alternative per hormone
  const [pickedEst, setPickedEst] = useState(0)   // index into alternatives
  const [pickedT, setPickedT] = useState(0)

  const estAlts = suggest.data?.estradiol?.alternatives || []
  const tAlts   = suggest.data?.testosterone?.alternatives || []

  // Reset picks when alternatives shift
  useEffect(() => {
    setPickedEst(0); setPickedT(0)
  }, [estAlts.length, tAlts.length])

  // Days-until calculations to flag pre-bag window
  const apptDate = visitChoice === 'create'
    ? newApptDate
    : (futureVisits.find(v => v.id === visitChoice)?.scheduled_date)
  const daysUntil = apptDate
    ? Math.round((new Date(apptDate + 'T00:00:00') - today) / 86400000)
    : null

  // 6. Save: create visit (if needed) + push dose card
  const save = useMutation({
    mutationFn: async () => {
      let visitId = visitChoice
      if (visitChoice === 'create') {
        const v = await api.post('/pellets/visits', {
          patient_id:    patient.id,
          visit_kind:    prior?.visit_id ? 'repeat' : 'initial',
          scheduled_date: newApptDate,
          location:      newApptLocation,
          notes:         'Created from Set Dose flow',
        }).then(r => r.data)
        visitId = v.id
      }
      // Build the dose-card payload from chosen alternatives
      const lines = []
      const est = estAlts[pickedEst]
      if (est) {
        for (const c of est.components) {
          lines.push({ dose_type_id: c.dose_type_id, quantity: c.count })
        }
      }
      const t = tAlts[pickedT]
      if (t) {
        for (const c of t.components) {
          lines.push({ dose_type_id: c.dose_type_id, quantity: c.count })
        }
      }
      if (lines.length === 0) {
        throw new Error('No doses to save — pick an alternative for each hormone.')
      }
      return api.put(`/pellets/visits/${visitId}/dose-card`,
                       { doses: lines }).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', patient.id] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || e.message || 'Save failed'),
  })

  const canSave = (targetEst > 0 || targetT > 0) &&
                   ((targetEst === 0) || estAlts[pickedEst]) &&
                   ((targetT === 0)   || tAlts[pickedT]) &&
                   (visitChoice !== 'create' || newApptDate)

  return (
    <SimpleDrawer title="Set dose" onClose={onClose}>
      <div className="text-[12px] text-gray-700 bg-plum-50/50 border border-plum-100 rounded p-2">
        {patient.patient_type === 'new'
          ? <>Enter the Dosagio-computed dose for this <strong>new</strong> patient.</>
          : <>Carrying forward the prior dose
             {prior?.inserted_at && <> from {fmt.date(prior.inserted_at.slice(0, 10))}</>}.{' '}
             Adjust as needed before saving.</>}
      </div>

      {/* Prior dose preview */}
      {prior && (prior.estradiol_mg > 0 || prior.testosterone_mg > 0) && (
        <div className="text-[11px] bg-gray-50 border border-gray-200 rounded p-2">
          <div className="font-semibold text-gray-700 mb-1">Prior dose</div>
          <div>
            Estradiol: <strong>{prior.estradiol_mg || 0}mg</strong>
            {' · '}
            Testosterone: <strong>{prior.testosterone_mg || 0}mg</strong>
          </div>
          <button className="text-plum-700 hover:underline mt-1"
                   onClick={() => {
                     setEstradiolMg(prior.estradiol_mg > 0 ? String(prior.estradiol_mg) : '')
                     setTestosteroneMg(prior.testosterone_mg > 0 ? String(prior.testosterone_mg) : '')
                   }}>
            Use prior dose
          </button>
        </div>
      )}

      {/* Target mg inputs */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Estradiol total (mg)</label>
          <input type="number" step="0.5" min="0"
                  className="input text-sm w-full font-mono"
                  value={estradiolMg}
                  onChange={e => setEstradiolMg(e.target.value)} />
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Testosterone total (mg)</label>
          <input type="number" step="0.5" min="0"
                  className="input text-sm w-full font-mono"
                  value={testosteroneMg}
                  onChange={e => setTestosteroneMg(e.target.value)} />
        </div>
      </div>

      {/* Visit picker */}
      <div className="border-t border-gray-100 pt-2 mt-2">
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Linked appointment *</label>
        <select className="input text-sm w-full" value={visitChoice}
                 onChange={e => setVisitChoice(e.target.value)}>
          {futureVisits.map(v => (
            <option key={v.id} value={v.id}>
              {fmt.date(v.scheduled_date)} · {LOC_LABEL[v.location] || v.location || '(no location)'}
            </option>
          ))}
          <option value="create">+ Create new appointment</option>
        </select>
        {visitChoice === 'create' && (
          <div className="grid grid-cols-2 gap-2 mt-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">New appt date *</label>
              <input type="date" className="input text-sm w-full"
                      value={newApptDate}
                      onChange={e => setNewApptDate(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
              <select className="input text-sm w-full" value={newApptLocation}
                       onChange={e => setNewApptLocation(e.target.value)}>
                <option value="white_plains">White Plains</option>
                <option value="brandywine">Brandywine</option>
                <option value="arlington">Arlington</option>
              </select>
            </div>
          </div>
        )}
        {daysUntil != null && (
          <div className={`text-[11px] mt-1 ${
            daysUntil < LEAD_DAYS
              ? 'text-amber-700'
              : 'text-gray-500'
          }`}>
            <Clock size={11} className="inline" /> {daysUntil} day{daysUntil === 1 ? '' : 's'} until appt
            {daysUntil < LEAD_DAYS && (
              <> · less than the {LEAD_DAYS}-day pre-bag window. Any out-of-stock
                pellet won't arrive in time.</>
            )}
          </div>
        )}
      </div>

      {/* Suggestions per hormone */}
      {(targetEst > 0 || targetT > 0) && (
        <div className="border-t border-gray-100 pt-2 mt-2">
          <div className="text-[10px] uppercase tracking-wide text-gray-500 font-semibold mb-1">
            Suggested combinations at {LOC_LABEL[visitLocation]}
          </div>
          {suggest.isLoading && (
            <div className="text-[12px] text-gray-400 italic">Checking inventory…</div>
          )}
          {targetEst > 0 && estAlts.length > 0 && (
            <HormoneAlternatives label="Estradiol" alts={estAlts}
                                   picked={pickedEst} onPick={setPickedEst} />
          )}
          {targetEst > 0 && estAlts.length === 0 && !suggest.isLoading && (
            <div className="text-[12px] text-red-700">
              No combination of available estradiol doses sums to {targetEst}mg.
              Adjust the target.
            </div>
          )}
          {targetT > 0 && tAlts.length > 0 && (
            <HormoneAlternatives label="Testosterone" alts={tAlts}
                                   picked={pickedT} onPick={setPickedT} />
          )}
          {targetT > 0 && tAlts.length === 0 && !suggest.isLoading && (
            <div className="text-[12px] text-red-700">
              No combination of available testosterone doses sums to {targetT}mg.
              Adjust the target.
            </div>
          )}
        </div>
      )}

      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={!canSave}
                     label={visitChoice === 'create'
                              ? 'Create appt + save dose'
                              : 'Save dose to appt'} />
    </SimpleDrawer>
  )
}


function HormoneAlternatives({ label, alts, picked, onPick }) {
  return (
    <div className="mb-2">
      <div className="text-[11px] font-medium text-gray-700 mb-1">{label}</div>
      <div className="space-y-1">
        {alts.map((alt, i) => {
          const isPicked = picked === i
          const inStock = alt.in_stock
          return (
            <label key={i}
                    className={`flex items-center gap-2 cursor-pointer border rounded p-2 text-[12px] ${
                      isPicked ? 'border-plum-400 bg-plum-50/60'
                      : 'border-gray-200 hover:bg-gray-50'
                    }`}>
              <input type="radio" checked={isPicked}
                      onChange={() => onPick(i)} />
              <div className="flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <strong>{alt.total_pellets} pellet{alt.total_pellets === 1 ? '' : 's'}</strong>
                  {alt.components.map((c, j) => (
                    <span key={j} className="text-[11px]">
                      {j > 0 && ' + '}{c.count}× {c.dose_mg}mg
                      {c.short > 0 && (
                        <span className="text-red-700 ml-0.5" title={`Short ${c.short}`}>
                          (short {c.short})
                        </span>
                      )}
                    </span>
                  ))}
                </div>
                {alt.components.some(c => (c.fifo_lots || []).length > 0) && (
                  <ul className="mt-1 space-y-0.5 text-[10px] text-gray-500">
                    {alt.components.map((c, j) => (
                      <li key={j}>
                        <span className="text-gray-700 font-medium">{c.count}× {c.dose_mg}mg</span>
                        {(c.fifo_lots || []).map((lot, k) => (
                          <span key={k} className="ml-1">
                            {k > 0 && ', '}
                            <span className="font-mono">
                              {lot.count > 1 ? `${lot.count}× ` : ''}lot {lot.qualgen_lot_number}
                            </span>
                            {lot.expiration_date && (
                              <span className="text-gray-400"> · exp {fmt.date(lot.expiration_date)}</span>
                            )}
                          </span>
                        ))}
                        {(c.fifo_lots || []).length === 0 && (
                          <span className="text-red-600 ml-1">no lot available</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {inStock ? (
                <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-green-100 text-green-700 shrink-0">
                  in stock
                </span>
              ) : (
                <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 shrink-0">
                  short
                </span>
              )}
            </label>
          )
        })}
      </div>
    </div>
  )
}


function toISODate(d) {
  return d.toISOString().slice(0, 10)
}


// ── Payment workflow ──────────────────────────────────────────────

function PaymentBox({ visit, qc }) {
  const sent = visit.payment_status === 'sent'
  const collected = visit.payment_status === 'collected'
  function invalidateAll() {
    qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
    qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
    qc.invalidateQueries({ queryKey: ['pellet-patients'] })
  }
  const klara = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/klara-sent`).then(r => r.data),
    onSuccess: invalidateAll,
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const collect = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/payment-collected`).then(r => r.data),
    onSuccess: invalidateAll,
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <div className="mt-3 border-t border-gray-100 pt-3 bg-amber-50/40 -mx-3 px-3 py-2 rounded">
      <div className="text-[11px] text-gray-700 font-semibold flex items-center gap-1 mb-1.5">
        <DollarSign size={12} className="text-amber-700"/>
        Payment workflow — ${visit.price_amount} via Klara → ModMed
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        {!sent && !collected && (
          <button className="btn-secondary text-[12px] flex items-center gap-1"
                  onClick={() => klara.mutate()}
                  disabled={klara.isPending}>
            <Send size={11}/> Send Klara payment link
          </button>
        )}
        {sent && !collected && (
          <>
            <span className="text-[11px] text-amber-700">
              ✓ Klara sent {visit.klara_sent_at && fmt.date(visit.klara_sent_at.slice(0,10))}
              {visit.klara_sent_by && ` by ${visit.klara_sent_by.split('@')[0]}`}
            </span>
            <button className="btn-primary text-[12px] flex items-center gap-1"
                    onClick={() => collect.mutate()}
                    disabled={collect.isPending}>
              <CheckCircle2 size={11}/> Mark Payment Collected
            </button>
          </>
        )}
        {collected && (
          <span className="text-[11px] text-green-700">
            ✓ Payment collected {visit.payment_collected_at && fmt.date(visit.payment_collected_at.slice(0,10))}
            {visit.payment_collected_by && ` by ${visit.payment_collected_by.split('@')[0]}`}
          </span>
        )}
      </div>
    </div>
  )
}




// ── Bag fill ─────────────────────────────────────────────────────

function BagFillDrawer({ visit, qc, onClose }) {
  const location = visit.location || 'white_plains'
  const plannedDoses = (visit.doses || []).filter(d => d.status === 'planned')

  // For each planned dose, fetch matching lots at this location
  const { data: lotData } = useQuery({
    queryKey: ['pellet-lots', location, 'all-types'],
    queryFn: () => api.get('/pellets/lots',
                            { params: { location } }).then(r => r.data),
  })
  const allLots = lotData?.lots || []

  // Compute the recommended lot per dose: earliest-expiration lot at this
  // location with enough stock. Returns a map { doseId → lot } (or null).
  function recommendFor(dose) {
    const candidates = allLots
      .filter(l => l.dose_type_id === dose.dose_type_id
                     && (l.balances?.[location] || 0) >= dose.quantity)
      .sort((a, b) => (a.expiration_date || '9999-12-31')
                          .localeCompare(b.expiration_date || '9999-12-31'))
    return candidates[0] || null
  }

  const [picks, setPicks] = useState({})
  const [userOverrode, setUserOverrode] = useState(() => new Set())

  // Auto-fill picks with the FIFO recommendation whenever lots load (or
  // whenever the planned dose list changes). Never overwrite a manual pick.
  useEffect(() => {
    if (allLots.length === 0) return
    setPicks(prev => {
      const next = { ...prev }
      let changed = false
      for (const d of plannedDoses) {
        if (userOverrode.has(d.id)) continue
        if (next[d.id]) continue
        const rec = recommendFor(d)
        if (rec) { next[d.id] = rec.id; changed = true }
      }
      return changed ? next : prev
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lotData, visit.id])

  function setPick(doseId, lotId) {
    setPicks(prev => ({ ...prev, [doseId]: lotId }))
    setUserOverrode(prev => new Set(prev).add(doseId))
  }

  const save = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/fill-bag`, {
      location,
      lines: plannedDoses
        .filter(d => picks[d.id])
        .map(d => ({ visit_dose_id: d.id, lot_id: picks[d.id] })),
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-lots'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Fill failed'),
  })

  return (
    <SimpleDrawer title={`Fill bag · ${LOC_LABEL[location] || location}`} onClose={onClose}>
      <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
        System suggests the lot with the <strong>earliest expiration</strong>{' '}
        (FIFO). Override if needed. Pulling decrements stock at{' '}
        <strong>{LOC_LABEL[location] || location}</strong> and writes one
        audit row per pull.
      </div>
      <div className="space-y-2">
        {plannedDoses.length === 0 && (
          <div className="text-[12px] text-gray-500 italic">
            No planned doses — set the dose card first.
          </div>
        )}
        {plannedDoses.map(d => {
          const candidates = allLots
            .filter(l => l.dose_type_id === d.dose_type_id
                           && (l.balances?.[location] || 0) >= d.quantity)
            .sort((a, b) => (a.expiration_date || '9999-12-31')
                                .localeCompare(b.expiration_date || '9999-12-31'))
          const recommended = candidates[0] || null
          const currentPick = picks[d.id] || ''
          const isFifoPick  = currentPick && recommended && currentPick === recommended.id
          return (
            <div key={d.id} className="border border-gray-200 rounded p-2">
              <div className="text-[12px] font-medium mb-1 flex items-baseline justify-between">
                <span>
                  {d.quantity}× {d.dose_label}
                  {d.is_controlled && (
                    <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
                  )}
                </span>
              </div>
              {candidates.length === 0 ? (
                <div className="text-[11px] text-red-700 italic">
                  No lots with ≥{d.quantity} doses at {LOC_LABEL[location] || location}.
                </div>
              ) : (
                <>
                  {recommended && (
                    <div className="text-[11px] mb-1 px-2 py-1 bg-green-50 border border-green-200 rounded">
                      <span className="text-green-800">
                        ✓ <strong>Use lot {recommended.qualgen_lot_number}</strong>
                        {' '}· exp {recommended.expiration_date}
                        {' '}· {recommended.balances?.[location] || 0} on hand
                      </span>
                      <span className="ml-1 text-[10px] uppercase tracking-wide text-green-700">
                        earliest expiry
                      </span>
                      {!isFifoPick && currentPick && (
                        <button type="button"
                                 className="ml-2 text-[10px] text-plum-700 underline"
                                 onClick={() => setPick(d.id, recommended.id)}>
                          use recommended
                        </button>
                      )}
                    </div>
                  )}
                  <select className="input text-[12px] w-full"
                           value={currentPick}
                           onChange={e => setPick(d.id, e.target.value)}>
                    <option value="">— pick a lot —</option>
                    {candidates.map((l, idx) => (
                      <option key={l.id} value={l.id}>
                        {idx === 0 ? '★ ' : ''}lot {l.qualgen_lot_number}
                        {' '}· exp {l.expiration_date}
                        {' '}· {l.balances?.[location] || 0} doses on hand
                        {idx === 0 ? ' (earliest expiry)' : ''}
                      </option>
                    ))}
                  </select>
                </>
              )}
            </div>
          )
        })}
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={plannedDoses.some(d => !picks[d.id])}
                     label="Fill bag · pull from stock" />
    </SimpleDrawer>
  )
}


// ── Insertion outcome ─────────────────────────────────────────────

function ConfirmInsertionDrawer({ visit, qc, onClose }) {
  // Per-line decisions for every still-proposed dose, plus an "add new"
  // bottom block. Defaults: insert all bagged doses as-is (the common
  // case). Provider can flip rows to Return or Swap, or add new doses.
  const proposed = (visit.doses || [])
    .filter(d => ['planned', 'pulled'].includes(d.status))
    .sort((a, b) => (a.position || 0) - (b.position || 0))

  // line state: { dose_id: { action, new_dose_type_id, new_lot_id, new_qty } }
  const [lines, setLines] = useState(() => Object.fromEntries(
    proposed.map(d => [d.id, {
      action:           'insert',
      new_dose_type_id: '',
      new_lot_id:       '',
      new_qty:          d.quantity,
    }])
  ))
  const [additions, setAdditions] = useState([])
  const [notes, setNotes] = useState('')
  const [error, setError] = useState(null)

  const { data: doseTypes } = useQuery({
    queryKey: ['pellet-dose-types-active'],
    queryFn: () => api.get('/pellets/dose-types').then(r => r.data),
    staleTime: 5 * 60_000,
  })
  // /pellets/dose-types returns the array directly, not { dose_types: [...] }
  const dtList = Array.isArray(doseTypes)
    ? doseTypes
    : (doseTypes?.dose_types || doseTypes?.types || [])
  const dtOptions = dtList.filter(t => t.is_active !== false)

  function setLine(doseId, patch) {
    setLines(prev => ({ ...prev, [doseId]: { ...prev[doseId], ...patch } }))
  }

  function addNewLine() {
    setAdditions(prev => [...prev, { dose_type_id: '', quantity: 1, lot_id: '', notes: '' }])
  }
  function updateAddition(i, patch) {
    setAdditions(prev => prev.map((a, j) => j === i ? { ...a, ...patch } : a))
  }
  function dropAddition(i) {
    setAdditions(prev => prev.filter((_, j) => j !== i))
  }

  const submit = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/confirm-insertion`, {
      lines: proposed.map(d => {
        const l = lines[d.id]
        const out = { dose_id: d.id, action: l.action }
        if (l.action === 'swap') {
          out.new_dose_type_id = l.new_dose_type_id
          if (l.new_lot_id)   out.new_lot_id   = l.new_lot_id
          if (l.new_qty != null && l.new_qty !== d.quantity)
            out.new_quantity = Number(l.new_qty)
        }
        return out
      }),
      additions: additions
        .filter(a => a.dose_type_id && a.quantity > 0)
        .map(a => ({
          dose_type_id: a.dose_type_id,
          quantity:     Number(a.quantity),
          ...(a.lot_id ? { lot_id: a.lot_id } : {}),
          ...(a.notes  ? { notes: a.notes  } : {}),
        })),
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      onClose()
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Submit failed'))
    },
  })

  const validSwapsOK = proposed.every(d => {
    const l = lines[d.id]
    return l.action !== 'swap' || !!l.new_dose_type_id
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-3 flex items-center justify-between z-10">
          <h2 className="text-[15px] font-semibold text-gray-900">
            Confirm what was inserted
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <p className="text-[12px] text-gray-600">
            For each bagged dose, choose what the provider actually did.
            Return/Swap automatically refunds the original pellet to stock.
          </p>

          <ul className="divide-y divide-gray-100">
            {proposed.map(d => {
              const l = lines[d.id]
              return (
                <li key={d.id} className="py-2 space-y-2">
                  <div className="flex items-baseline justify-between flex-wrap gap-2">
                    <div className="text-[13px]">
                      <span className="font-medium">{d.quantity}×</span>{' '}
                      {d.dose_label}{' '}
                      <span className="text-[11px] text-gray-500 font-mono">
                        lot {d.qualgen_lot}
                      </span>
                      {d.lot_expiration_date && (
                        <span className="text-[10px] text-gray-400 ml-1">
                          exp {fmt.date(d.lot_expiration_date)}
                        </span>
                      )}
                    </div>
                    <div className="flex gap-1 text-[11px] flex-wrap">
                      {['insert', 'return', 'swap'].map(a => (
                        <button key={a}
                                onClick={() => setLine(d.id, { action: a })}
                                className={`px-2 py-1 rounded border ${
                                  l.action === a
                                    ? a === 'insert' ? 'bg-emerald-600 text-white border-emerald-600'
                                      : a === 'return' ? 'bg-amber-600 text-white border-amber-600'
                                      : 'bg-violet-600 text-white border-violet-600'
                                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'
                                }`}>
                          {a === 'insert' ? '✓ Insert' : a === 'return' ? '↩ Return' : '🔄 Swap'}
                        </button>
                      ))}
                      <button onClick={() => setAdditions(prev => [
                                ...prev,
                                { dose_type_id: d.dose_type_id, quantity: 1, lot_id: '',
                                  notes: `Provider added a 2nd ${d.dose_label}` },
                              ])}
                              className="px-2 py-1 rounded border bg-white text-emerald-700 border-emerald-300 hover:bg-emerald-50"
                              title="Provider gave an additional pellet of this same dose">
                        + Add 1 more
                      </button>
                    </div>
                  </div>
                  {l.action === 'swap' && (
                    <div className="bg-violet-50/40 border border-violet-200 rounded p-2 space-y-1.5">
                      <div className="text-[11px] uppercase tracking-wide text-violet-700 font-semibold">
                        Swap to
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-[1fr_100px] gap-2">
                        <select className="input text-[12px]"
                                value={l.new_dose_type_id}
                                onChange={e => setLine(d.id, { new_dose_type_id: e.target.value })}>
                          <option value="">— select dose type —</option>
                          {dtOptions.map(t => (
                            <option key={t.id} value={t.id}>{t.label}</option>
                          ))}
                        </select>
                        <input type="number" min="1" className="input text-[12px] font-mono"
                                placeholder="Qty"
                                value={l.new_qty}
                                onChange={e => setLine(d.id, { new_qty: e.target.value })} />
                      </div>
                      {l.new_dose_type_id && (
                        <LotPickerForType
                          doseTypeId={l.new_dose_type_id}
                          location={visit.location}
                          minQty={Number(l.new_qty) || 1}
                          selected={l.new_lot_id}
                          onSelect={(id) => setLine(d.id, { new_lot_id: id })} />
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>

          {/* Additions */}
          <div className="border-t border-gray-200 pt-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[12px] font-semibold text-gray-800">
                Add new dose(s) (provider added in-room)
              </div>
              <button className="text-[11px] text-plum-700 hover:underline flex items-center gap-1"
                      onClick={addNewLine}>
                <Plus size={11}/> Add row
              </button>
            </div>
            {additions.length === 0 && (
              <div className="text-[11px] text-gray-500 italic">
                No additions. Use this when the provider grabbed an extra pellet
                that wasn't in the bag.
              </div>
            )}
            <ul className="space-y-2">
              {additions.map((a, i) => (
                <li key={i} className="bg-gray-50 border border-gray-200 rounded p-2 space-y-1.5">
                  <div className="grid grid-cols-[1fr_80px_24px] gap-2">
                    <select className="input text-[12px]"
                            value={a.dose_type_id}
                            onChange={e => updateAddition(i, { dose_type_id: e.target.value })}>
                      <option value="">— select dose type —</option>
                      {dtOptions.map(t => (
                        <option key={t.id} value={t.id}>{t.label}</option>
                      ))}
                    </select>
                    <input type="number" min="1" className="input text-[12px] font-mono"
                            value={a.quantity}
                            onChange={e => updateAddition(i, { quantity: e.target.value })} />
                    <button onClick={() => dropAddition(i)}
                            className="text-red-600 hover:bg-red-50 rounded p-1">
                      <Trash2 size={12} />
                    </button>
                  </div>
                  {a.dose_type_id && (
                    <LotPickerForType
                      doseTypeId={a.dose_type_id}
                      location={visit.location}
                      minQty={Number(a.quantity) || 1}
                      selected={a.lot_id}
                      onSelect={(id) => updateAddition(i, { lot_id: id })} />
                  )}
                  <input type="text" className="input text-[12px]"
                          placeholder="Note (optional)"
                          value={a.notes || ''}
                          onChange={e => updateAddition(i, { notes: e.target.value })} />
                </li>
              ))}
            </ul>
          </div>

          {/* Overall note */}
          <div>
            <label className="text-[11px] uppercase tracking-wide text-gray-500 font-medium block mb-1">
              Visit note (optional)
            </label>
            <textarea className="input text-[12px] w-full"
                       rows={2}
                       value={notes}
                       onChange={e => setNotes(e.target.value)} />
          </div>

          {error && (
            <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onClose} className="btn-secondary text-sm">Cancel</button>
            <button onClick={() => submit.mutate()}
                    disabled={submit.isPending || !validSwapsOK}
                    className="btn-primary text-sm">
              {submit.isPending ? 'Confirming…' : 'Confirm insertion'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function LotPickerForType({ doseTypeId, location, minQty, selected, onSelect }) {
  const { data, isLoading } = useQuery({
    queryKey: ['lot-options', doseTypeId, location],
    queryFn: () => api.get('/pellets/lots', {
      params: { dose_type_id: doseTypeId, location, in_stock_only: true },
    }).then(r => r.data),
    enabled: !!doseTypeId,
  })
  const lots = (data?.lots || []).filter(l => (l.balances?.[location] || 0) >= minQty)
  if (!doseTypeId) return null
  if (isLoading) return <div className="text-[10px] text-gray-500 italic">Loading lots…</div>
  if (lots.length === 0) {
    return (
      <div className="text-[10px] text-amber-700">
        No lot has ≥{minQty} at {location}.
      </div>
    )
  }
  return (
    <select className="input text-[12px] w-full"
             value={selected || ''}
             onChange={e => onSelect(e.target.value || null)}>
      <option value="">FIFO (auto-pick earliest expiry)</option>
      {lots.map(l => (
        <option key={l.id} value={l.id}>
          {l.qualgen_lot_number}
          {l.expiration_date ? ` · exp ${l.expiration_date}` : ''}
          {' · '}{l.balances?.[location] || 0} on hand
        </option>
      ))}
    </select>
  )
}


function InsertionOutcomeDrawer({ visit, qc, onClose }) {
  const [outcome, setOutcome] = useState('perfect')
  const [notes, setNotes] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/insert`,
                                 { outcome, notes: notes || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <SimpleDrawer title="Record insertion outcome" onClose={onClose}>
      <div className="space-y-1">
        <OutcomeOption value="perfect" current={outcome} setCurrent={setOutcome}
                        label="Perfect insertion"
                        sub="All pulled doses placed — close out and bill" />
        <OutcomeOption value="rescheduled" current={outcome} setCurrent={setOutcome}
                        label="Rescheduled"
                        sub="Returns all pulled doses to stock; visit remains open" />
        <OutcomeOption value="cancelled" current={outcome} setCurrent={setOutcome}
                        label="Cancelled"
                        sub="Returns all pulled doses to stock; closes visit" />
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                  value={notes} onChange={e => setNotes(e.target.value)}
                  placeholder="Optional explanation (esp. for reschedule / cancel)" />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     label="Record outcome" />
    </SimpleDrawer>
  )
}


function OutcomeOption({ value, current, setCurrent, label, sub }) {
  const active = current === value
  return (
    <label className={`flex items-baseline gap-2 cursor-pointer p-2 rounded border ${
      active ? 'border-plum-400 bg-plum-50/50' : 'border-gray-200 hover:bg-gray-50'
    }`}>
      <input type="radio" name="outcome" value={value} checked={active}
              onChange={() => setCurrent(value)} />
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        <div className="text-[11px] text-gray-500">{sub}</div>
      </div>
    </label>
  )
}


// ── Mid-procedure add ─────────────────────────────────────────────

function MidProcedureAddDrawer({ visit, qc, onClose }) {
  const location = visit.location || 'white_plains'
  const { data: types = [] } = useQuery({
    queryKey: ['pellet-dose-types'],
    queryFn: () => api.get('/pellets/dose-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: lotData } = useQuery({
    queryKey: ['pellet-lots', location, 'all-types'],
    queryFn: () => api.get('/pellets/lots', { params: { location } }).then(r => r.data),
  })
  const lots = lotData?.lots || []

  const [doseTypeId, setDoseTypeId] = useState('')
  const [lotId, setLotId] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [notes, setNotes] = useState('')

  const candidateLots = lots.filter(l => l.dose_type_id === doseTypeId)

  const save = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/add-dose`, {
      dose_type_id: doseTypeId,
      lot_id: lotId,
      quantity: Number(quantity),
      location,
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })

  return (
    <SimpleDrawer title="Add mid-procedure dose" onClose={onClose}>
      <div className="text-[12px] text-gray-600 bg-violet-50/50 border border-violet-100 rounded p-2">
        Provider decided to add another pellet during the insertion.
        Pulls from <strong>{LOC_LABEL[location]}</strong>, decrements
        stock, and writes an audit row. Schedule III chain-of-custody is
        preserved.
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Dose</label>
          <select className="input text-sm w-full"
                   value={doseTypeId}
                   onChange={e => { setDoseTypeId(e.target.value); setLotId('') }}>
            <option value="">— choose dose —</option>
            {types.map(t => (
              <option key={t.id} value={t.id}>
                {t.label}{t.is_controlled ? ' (Sch III)' : ''}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Quantity</label>
          <input type="number" min="1" className="input text-sm w-full font-mono"
                  value={quantity}
                  onChange={e => setQuantity(e.target.value)} />
        </div>
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Lot</label>
        <select className="input text-sm w-full" value={lotId}
                 onChange={e => setLotId(e.target.value)} disabled={!doseTypeId}>
          <option value="">— choose lot —</option>
          {candidateLots.map(l => (
            <option key={l.id} value={l.id}>
              lot {l.qualgen_lot_number} · exp {l.expiration_date} · {l.balances?.[location] || 0} doses
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                  value={notes} onChange={e => setNotes(e.target.value)}
                  placeholder="Why was the dose increased?" />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={!doseTypeId || !lotId || quantity <= 0}
                     label="Pull + add to visit" />
    </SimpleDrawer>
  )
}


// ── Mid-procedure dispose ─────────────────────────────────────────

function MidProcedureDisposeDrawer({ visit, dose, qc, onClose }) {
  const [reason, setReason] = useState('dropped')
  const [witness, setWitness] = useState('')
  const [notes, setNotes] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/pellets/visits/${visit.id}/dispose-dose`, {
      visit_dose_id: dose.id,
      reason,
      witness_user: witness || null,
      location: visit.location || 'white_plains',
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-patient', visit.patient_id] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Dispose failed'),
  })

  return (
    <SimpleDrawer title="Dispose dose (biohazard)" onClose={onClose}>
      <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
        Sends <strong>{dose.quantity}× {dose.dose_label}</strong> (lot{' '}
        <span className="font-mono">{dose.qualgen_lot}</span>) to the
        biohazard sharps container. We don't contact Qualgen — the
        practice eats the loss.
      </div>
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Reason</label>
        <select className="input text-sm w-full" value={reason}
                 onChange={e => setReason(e.target.value)}>
          <option value="dropped">Dropped on floor</option>
          <option value="broken">Broken / damaged</option>
          <option value="other">Other (notes required)</option>
        </select>
      </div>
      {dose.is_controlled && (
        <div className="border border-amber-200 bg-amber-50/50 rounded p-2">
          <div className="text-[11px] text-amber-800 font-semibold flex items-center gap-1 mb-1">
            <Shield size={11} /> Schedule III witness required
          </div>
          <input className="input text-[12px] w-full"
                  placeholder="Witness email (must be a different person)"
                  value={witness} onChange={e => setWitness(e.target.value)} />
        </div>
      )}
      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">
          Notes {reason === 'other' && <span className="text-red-600">*</span>}
        </label>
        <textarea className="input text-[12px] w-full" rows={2}
                   value={notes} onChange={e => setNotes(e.target.value)} />
      </div>
      <DrawerFooter onClose={onClose} onSave={() => save.mutate()}
                     saving={save.isPending}
                     disabled={(dose.is_controlled && !witness.trim()) ||
                                (reason === 'other' && !notes.trim())}
                     label="Confirm disposal" />
    </SimpleDrawer>
  )
}


// Drawer scaffolding -------------------------------------------------

function SimpleDrawer({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">{title}</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          {children}
        </div>
      </div>
    </div>
  )
}


function DrawerFooter({ onClose, onSave, saving, disabled, label = 'Save' }) {
  return (
    <div className="sticky bottom-0 bg-white border-t border-border-subtle -mx-5 px-5 py-3 mt-3 flex justify-end gap-2">
      <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
      <button className="btn-primary text-sm flex items-center gap-1"
              onClick={onSave} disabled={disabled || saving}>
        <Save size={12}/> {saving ? 'Saving…' : label}
      </button>
    </div>
  )
}
