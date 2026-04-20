import { useState } from 'react'
import MoneyInput from './MoneyInput'

/**
 * Editable list of adjustments (CARC-coded breakdown rows).
 *
 * Works for both claim and service-line adjustments. The parent tracks the
 * full array; this component renders rows, inline-edits them, and flags ops.
 *
 * Props:
 * - value: Array<{ id?: string, tempId?: number, op: 'none'|'edited'|'deleted'|'new',
 *                  group_code, reason_code, amount, reason_description, quantity? }>
 * - onChange: (newArray) => void
 * - disabled?: boolean
 */
export default function AdjustmentList({ value, onChange, disabled }) {
  const [nextTempId, setNextTempId] = useState(1)

  function updateRow(idx, patch) {
    const next = value.slice()
    const row = { ...next[idx], ...patch }
    if (row.op === 'none' || row.op === 'edited') {
      row.op = 'edited'
    }
    next[idx] = row
    onChange(next)
  }

  function markDeleted(idx) {
    const next = value.slice()
    if (next[idx].op === 'new') {
      next.splice(idx, 1)  // never sent to server — just drop it
    } else {
      next[idx] = { ...next[idx], op: 'deleted' }
    }
    onChange(next)
  }

  function undoDelete(idx) {
    const next = value.slice()
    next[idx] = { ...next[idx], op: 'none' }
    onChange(next)
  }

  function addRow() {
    onChange([
      ...value,
      {
        tempId: nextTempId, op: 'new',
        group_code: '', reason_code: '', amount: 0, reason_description: '',
      },
    ])
    setNextTempId(n => n + 1)
  }

  const visible = value.filter(r => r.op !== 'deleted')
  const deleted = value.map((r, i) => ({ ...r, _i: i })).filter(r => r.op === 'deleted')

  return (
    <div className="space-y-1">
      {visible.length === 0 && deleted.length === 0 && (
        <div className="text-[11px] text-muted italic">No adjustments.</div>
      )}

      {value.map((row, idx) => {
        if (row.op === 'deleted') return null
        return (
          <div key={row.id || `new-${row.tempId}`} className="flex gap-1 items-center">
            <input
              className="input w-14 py-0.5 text-[11px] font-mono"
              placeholder="CO"
              value={row.group_code || ''}
              onChange={(e) => updateRow(idx, { group_code: e.target.value })}
              disabled={disabled}
            />
            <input
              className="input w-16 py-0.5 text-[11px] font-mono"
              placeholder="45"
              value={row.reason_code || ''}
              onChange={(e) => updateRow(idx, { reason_code: e.target.value })}
              disabled={disabled}
            />
            <div className="w-24">
              <MoneyInput
                value={row.amount ?? 0}
                onChange={(v) => updateRow(idx, { amount: v })}
                disabled={disabled}
              />
            </div>
            <input
              className="input flex-1 py-0.5 text-[11px]"
              placeholder="Description"
              value={row.reason_description || ''}
              onChange={(e) => updateRow(idx, { reason_description: e.target.value })}
              disabled={disabled}
            />
            <button
              type="button"
              className="text-[11px] text-danger px-1"
              onClick={() => markDeleted(idx)}
              disabled={disabled}
              title="Remove"
            >✗</button>
          </div>
        )
      })}

      {deleted.map(row => (
        <div key={`d-${row.id || row.tempId}`} className="flex items-center gap-2 text-[11px] text-muted line-through">
          <span>{row.group_code}-{row.reason_code} ${row.amount} {row.reason_description}</span>
          <button type="button" className="underline no-underline-hover"
                  onClick={() => undoDelete(row._i)}>undo</button>
        </div>
      ))}

      <button
        type="button"
        className="text-[11px] text-plum-600 underline mt-1"
        onClick={addRow}
        disabled={disabled}
      >+ Add adjustment</button>
    </div>
  )
}
