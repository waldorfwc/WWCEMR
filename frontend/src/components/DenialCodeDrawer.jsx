import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  X, Wrench, NotebookPen, History, Save, Sparkles, Search,
} from 'lucide-react'
import api, { fmt } from '../utils/api'

/**
 * Right-side drawer for denial-code lookup.
 *
 *   mode: 'single'  — show one CARC or RARC with its full enrichment + WWC notes
 *   mode: 'combo'   — show combined synthesis for (group_code, CARC, RARCs)
 *
 * Always includes a "Jump to a code" autocomplete at the top so users
 * can keep exploring adjacent codes without closing the drawer.
 */
export default function DenialCodeDrawer({ open, onClose, initialRequest }) {
  const [request, setRequest] = useState(initialRequest)

  // When the drawer is (re)opened with a new request, reset state.
  useEffect(() => {
    if (open && initialRequest) setRequest(initialRequest)
  }, [open, initialRequest])

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-40"
          onClick={onClose}
          aria-hidden
        />
      )}
      <aside
        className={`fixed top-0 right-0 h-full w-[440px] bg-white border-l border-gray-200 shadow-2xl z-50 transition-transform duration-200 overflow-y-auto ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {request && (
          <DrawerContent
            request={request}
            onJumpTo={r => setRequest(r)}
            onClose={onClose}
          />
        )}
      </aside>
    </>
  )
}


function DrawerContent({ request, onJumpTo, onClose }) {
  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] uppercase tracking-wide text-gray-500">
          {request.mode === 'combo' ? 'Combined denial codeset' : 'Denial code reference'}
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 p-1 rounded hover:bg-gray-100"
          aria-label="Close"
        >
          <X size={16} />
        </button>
      </div>

      <JumpToCode onPick={onJumpTo} />

      <div className="mt-4">
        {request.mode === 'combo' && <ComboView request={request} />}
        {request.mode === 'group' && <GroupView request={request} />}
        {request.mode === 'single' && <SingleCodeView request={request} />}
      </div>
    </div>
  )
}


function GroupView({ request }) {
  const g = GROUP_CODES[request.group_code]
  if (!g) return <div className="text-xs text-red-600">Unknown group code: {request.group_code}</div>
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <GroupBadge code={request.group_code} />
        <span className="text-sm font-semibold text-gray-900">{g.name}</span>
      </div>
      <p className="text-xs text-gray-800 mt-2 leading-relaxed font-medium">{g.short}</p>

      <Section label="What it means">
        <p className="text-xs text-gray-800 leading-relaxed">{g.detail}</p>
      </Section>

      <Section label="Disposition" icon={Wrench}>
        <p className="text-xs text-gray-800 leading-relaxed">{g.disposition}</p>
      </Section>

      <div className="mt-4 pt-3 border-t border-gray-100">
        <div className="text-[11px] uppercase font-semibold text-gray-500 mb-2">All group codes</div>
        <div className="space-y-2">
          {Object.entries(GROUP_CODES).map(([k, info]) => (
            <div key={k} className="flex items-start gap-2 text-xs">
              <GroupBadge code={k} />
              <div>
                <div className="font-medium text-gray-800">{info.name}</div>
                <div className="text-gray-500 text-[11px]">{info.short}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}


/* -------------------------- JUMP-TO-CODE INPUT ------------------------- */

function JumpToCode({ onPick }) {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const { data } = useQuery({
    queryKey: ['adjustment-codes-typeahead', q],
    queryFn: () => api.get('/adjustment-codes', {
      params: { q: q || undefined, per_page: 8 },
    }).then(r => r.data),
    enabled: focused,
    staleTime: 60_000,
  })
  const items = data?.items || []

  return (
    <div className="relative">
      <Search size={12} className="absolute left-2.5 top-2.5 text-gray-400" />
      <input
        className="w-full pl-7 pr-2 py-1.5 border border-gray-200 rounded text-xs focus:outline-none focus:ring-1 focus:ring-plum-400"
        placeholder="Jump to another code… (e.g. 197, M86)"
        value={q}
        onChange={e => setQ(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 150)}
      />
      {focused && items.length > 0 && (
        <div className="absolute left-0 right-0 mt-1 max-h-72 overflow-y-auto bg-white border border-gray-200 rounded shadow-lg z-10">
          {items.map(row => (
            <button
              key={`${row.code_type}-${row.code}`}
              className="w-full text-left px-2.5 py-1.5 hover:bg-plum-50 text-xs border-b border-gray-100 last:border-0"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onPick({ mode: 'single', code_type: row.code_type, code: row.code })
                setQ('')
              }}
            >
              <span className="font-mono font-semibold text-plum-600">
                {row.code_type} {row.code}
              </span>
              <span className="text-gray-700 ml-2">
                {row.official_verbiage.slice(0, 80)}
                {row.official_verbiage.length > 80 ? '…' : ''}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}


/* ----------------------------- SINGLE CODE ----------------------------- */

function SingleCodeView({ request }) {
  const { code_type, code } = request
  const { data, isLoading } = useQuery({
    queryKey: ['adjustment-code', code_type, code],
    queryFn: () => api.get(`/adjustment-codes/${code_type}/${code}`).then(r => r.data),
  })

  if (isLoading) return <Loading />
  if (!data) return <div className="text-xs text-red-600">Code not found.</div>

  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] uppercase font-semibold text-plum-600">{data.code_type}</span>
        <span className="font-mono text-lg font-bold text-gray-900">{data.code}</span>
      </div>
      <p className="text-sm text-gray-800 mt-1">{data.official_verbiage}</p>

      <Section label="What it means">
        {data.plain_english
          ? <p className="text-xs text-gray-800 leading-relaxed">{data.plain_english}</p>
          : <Empty text="Plain-English explanation not yet generated." />}
      </Section>

      <Section label="How to fix" icon={Wrench}>
        {data.how_to_fix
          ? <pre className="whitespace-pre-wrap font-sans text-xs text-gray-800 leading-relaxed">{data.how_to_fix}</pre>
          : <Empty text="Fix guidance not yet generated." />}
      </Section>

      <WwcNotesSection row={data} />
    </div>
  )
}


/* ----------------------------- COMBO ----------------------------- */

function ComboView({ request }) {
  const { group_code, carc, rarcs = [] } = request
  const { data, isLoading, error } = useQuery({
    queryKey: ['adjustment-code-synth', group_code, carc, [...rarcs].sort().join(',')],
    queryFn: () =>
      api.post('/adjustment-codes/synthesize', {
        group_code, carc, rarcs,
      }).then(r => r.data),
  })

  return (
    <div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <GroupBadge code={group_code} />
        <CodeChip type="CARC" code={carc} />
        {rarcs.map(r => <CodeChip key={r} type="RARC" code={r} />)}
        {data?.from_cache && (
          <span className="ml-auto text-[11px] text-gray-400 italic" title="Combined explanation was served from cache">cached</span>
        )}
      </div>

      <div className="mt-3 border border-plum-100 bg-plum-50/40 rounded p-3">
        <div className="flex items-center gap-1 text-[11px] uppercase font-semibold text-plum-700 mb-1">
          <Sparkles size={11} /> Combined meaning
        </div>
        {isLoading && <Loading />}
        {error && <div className="text-xs text-red-600">{error.response?.data?.detail || 'Synthesis failed.'}</div>}
        {data && (
          <>
            <p className="text-xs text-gray-800 leading-relaxed">{data.plain_english}</p>
            <div className="flex items-center gap-1 text-[11px] uppercase font-semibold text-plum-700 mt-3 mb-1">
              <Wrench size={11} /> Combined fix plan
            </div>
            <pre className="whitespace-pre-wrap font-sans text-xs text-gray-800 leading-relaxed">{data.how_to_fix}</pre>
          </>
        )}
      </div>

      {/* Also let the user drill into each individual code — cheap links */}
      <div className="mt-3 pt-3 border-t border-gray-100">
        <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">
          Individual codes in this combo
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {[{ t: 'CARC', c: carc }, ...rarcs.map(r => ({ t: 'RARC', c: r }))].map(({ t, c }) => (
            <span key={`${t}-${c}`} className="text-[10px] text-gray-500">
              <span className="font-mono text-plum-600">{t} {c}</span>
              {' — '}
              <span className="text-gray-400">search above to open</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}


/* ----------------------------- NOTES ----------------------------- */

function WwcNotesSection({ row }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(row.wwc_notes || '')
  const [showHistory, setShowHistory] = useState(false)

  useEffect(() => { setDraft(row.wwc_notes || '') }, [row.wwc_notes])

  const save = useMutation({
    mutationFn: (body) =>
      api.put(`/adjustment-codes/${row.code_type}/${row.code}/notes`, { body })
         .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['adjustment-code', row.code_type, row.code] })
      qc.invalidateQueries({ queryKey: ['adjustment-codes-typeahead'] })
      setEditing(false)
    },
  })

  return (
    <div className="mt-4 pt-3 border-t border-gray-200">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1 text-[11px] font-semibold uppercase text-gray-500">
          <NotebookPen size={11} /> WWC Notes
          {row.wwc_notes_updated_by && (
            <span className="ml-2 font-normal normal-case text-gray-400">
              — {row.wwc_notes_updated_by}
              {row.wwc_notes_updated_at && <> · {fmt.date(row.wwc_notes_updated_at)}</>}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowHistory(v => !v)}
            className="text-[10px] text-gray-500 hover:text-gray-700 flex items-center gap-1"
          >
            <History size={11} /> History
          </button>
          {!editing && (
            <button
              onClick={() => setEditing(true)}
              className="text-[10px] text-plum-600 hover:underline"
            >
              {row.wwc_notes ? 'Edit' : 'Add notes'}
            </button>
          )}
        </div>
      </div>

      {editing ? (
        <div>
          <textarea
            className="w-full text-xs border border-gray-200 rounded p-2 focus:outline-none focus:ring-1 focus:ring-plum-400"
            rows={4}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            placeholder="WWC billing notes for this code. Don't include patient identifiers."
            autoFocus
          />
          <div className="flex gap-2 mt-2">
            <button
              onClick={() => save.mutate(draft)}
              disabled={save.isPending}
              className="btn-primary text-xs flex items-center gap-1 disabled:opacity-50"
            >
              <Save size={11} />
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => { setDraft(row.wwc_notes || ''); setEditing(false) }}
              className="btn-secondary text-xs"
            >
              Cancel
            </button>
            {save.isError && (
              <span className="text-xs text-red-600 self-center">
                {save.error?.response?.data?.detail || 'Save failed'}
              </span>
            )}
          </div>
        </div>
      ) : row.wwc_notes ? (
        <pre className="whitespace-pre-wrap font-sans text-xs text-gray-800 leading-relaxed">{row.wwc_notes}</pre>
      ) : (
        <p className="text-gray-400 italic text-xs">No WWC notes yet.</p>
      )}

      {showHistory && <NotesHistory row={row} />}
    </div>
  )
}


function NotesHistory({ row }) {
  const { data, isLoading } = useQuery({
    queryKey: ['adjustment-code-notes-history', row.code_type, row.code],
    queryFn: () =>
      api.get(`/adjustment-codes/${row.code_type}/${row.code}/notes/history`)
         .then(r => r.data),
  })
  if (isLoading) return <div className="mt-2 text-[10px] text-gray-400">Loading history…</div>
  const revs = data?.revisions || []
  if (revs.length === 0) {
    return <div className="mt-2 text-[10px] text-gray-400 italic">No saved revisions yet.</div>
  }
  return (
    <div className="mt-2 space-y-1.5">
      <div className="text-[11px] uppercase tracking-wide text-gray-400">
        Revision history ({revs.length})
      </div>
      {revs.map((r, i) => (
        <details key={r.id} className="border border-gray-100 rounded bg-white">
          <summary className="cursor-pointer px-2 py-1 text-[10px] text-gray-600 hover:bg-gray-50">
            {i === 0 && <span className="text-[11px] font-semibold text-green-700 mr-1.5">CURRENT</span>}
            {r.saved_by} · {fmt.dateTime(r.saved_at)}
          </summary>
          <pre className="whitespace-pre-wrap font-sans text-[11px] text-gray-700 px-3 py-2 border-t border-gray-100 leading-relaxed">
            {r.body || <span className="italic text-gray-400">(cleared)</span>}
          </pre>
        </details>
      ))}
    </div>
  )
}


/* ----------------------------- PRIMITIVES ----------------------------- */

function Section({ label, icon: Icon, children }) {
  return (
    <div className="mt-3">
      <div className="flex items-center gap-1 text-[11px] uppercase font-semibold text-gray-500 mb-1">
        {Icon && <Icon size={11} />} {label}
      </div>
      {children}
    </div>
  )
}

function Empty({ text }) {
  return <p className="text-gray-400 italic text-xs">{text}</p>
}

function Loading() {
  return <div className="text-xs text-gray-400">Loading…</div>
}

const GROUP_STYLES = {
  CO: 'bg-red-100 text-red-800 border-red-200',
  PR: 'bg-amber-100 text-amber-800 border-amber-200',
  OA: 'bg-blue-100 text-blue-800 border-blue-200',
  PI: 'bg-gray-100 text-gray-700 border-gray-300',
  CR: 'bg-purple-100 text-purple-800 border-purple-200',
}

export const GROUP_CODES = {
  CO: {
    name: 'Contractual Obligation',
    short: 'Provider eats it per contract — cannot balance-bill the patient.',
    detail: "Reductions the provider is contractually required to accept under their participating-provider agreement with the payer. These amounts CANNOT be billed to the patient. Examples: fee-schedule write-offs (CARC 45), no-auth denials (CARC 197), timely-filing (CARC 29).",
    disposition: "Appeal if you have grounds; otherwise post a contractual adjustment and write off.",
  },
  PR: {
    name: 'Patient Responsibility',
    short: 'Patient owes this — copay, coinsurance, deductible.',
    detail: "The payer is telling you the patient is responsible for this portion. You CAN and SHOULD bill the patient. Common examples: CARC 1 (deductible), CARC 2 (coinsurance), CARC 3 (copay). Non-covered charges (CARC 96) under PR mean the patient owes it only if a valid ABN was signed in advance.",
    disposition: "Bill the patient. Generate a statement.",
  },
  OA: {
    name: 'Other Adjustment',
    short: 'Usually coordination of benefits / secondary payer processing.',
    detail: "Reductions that don't fit CO or PR. Most commonly: the primary payer has already paid, so the claim needs to move to the secondary payer (CARC 23, 'impact of prior payer adjudication').",
    disposition: "Check secondary insurance on file, rebill to the next payer in order.",
  },
  PI: {
    name: 'Payer-Initiated Reduction',
    short: 'Payer audit / recoupment — rare.',
    detail: "Reductions the payer makes on its own initiative, usually after an audit, overpayment recovery, or a regulatory adjustment. Comes with a letter from the payer explaining the reduction.",
    disposition: "Read the payer's letter carefully; appeal only with documentation refuting their finding.",
  },
  CR: {
    name: 'Corrections and Reversals',
    short: 'A prior posting is being corrected or reversed.',
    detail: "Used when the payer is undoing or correcting a previous adjustment. Usually tied to a reprocessed claim.",
    disposition: "Verify the corrected amounts match the payer's expectation; the balance on the claim should self-reconcile after the correction posts.",
  },
}

export function GroupBadge({ code, onClick }) {
  const cls = GROUP_STYLES[code] || 'bg-gray-100 text-gray-700 border-gray-200'
  const g = GROUP_CODES[code]
  const title = g ? `${code} — ${g.name}: ${g.short}` : code
  const cursor = onClick ? 'cursor-pointer hover:brightness-95' : ''
  return (
    <span
      className={`px-1.5 py-0.5 rounded font-mono text-[10px] font-bold border ${cls} ${cursor}`}
      onClick={onClick}
      title={title}
    >
      {code || '—'}
    </span>
  )
}

export function CodeChip({ type, code, onClick }) {
  const base = type === 'CARC'
    ? 'bg-amber-50 border-amber-200 text-amber-800 hover:bg-amber-100'
    : 'bg-blue-50 border-blue-200 text-blue-800 hover:bg-blue-100'
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2 py-0.5 rounded-full border font-mono text-[10px] cursor-pointer ${base}`}
    >
      {type} {code}
    </button>
  )
}
