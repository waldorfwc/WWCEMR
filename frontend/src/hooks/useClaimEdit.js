import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Orchestrates a sequential save for the claim edit drawer.
 *
 * save({ claimId, fieldsDiff, adjustments }):
 *  - claim PATCH if fieldsDiff has keys
 *  - adjustments: POST all 'new' rows, PATCH all 'edited', DELETE all 'deleted'
 *
 * Exposes { save, saving, error, step } where `step` is a progress string
 * like "2/4" during execution.
 */
export function useClaimEdit() {
  const queryClient = useQueryClient()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [step, setStep] = useState(null)

  async function save({ claimId, fieldsDiff, adjustments }) {
    setSaving(true)
    setError(null)

    // Build ordered operation list
    const ops = []
    if (fieldsDiff && Object.keys(fieldsDiff).length > 0) {
      ops.push({ kind: 'claim-patch', body: fieldsDiff })
    }
    for (const a of adjustments) {
      if (a.op === 'new') ops.push({ kind: 'adj-post', body: _adjBody(a), tempId: a.tempId })
      else if (a.op === 'edited') ops.push({ kind: 'adj-patch', id: a.id, body: _adjBody(a) })
      else if (a.op === 'deleted') ops.push({ kind: 'adj-delete', id: a.id })
    }

    for (let i = 0; i < ops.length; i++) {
      const op = ops[i]
      setStep(`${i + 1}/${ops.length}`)
      try {
        if (op.kind === 'claim-patch') {
          await api.patch(`/claims/${claimId}`, op.body)
        } else if (op.kind === 'adj-post') {
          await api.post(`/claims/${claimId}/adjustments`, op.body)
        } else if (op.kind === 'adj-patch') {
          await api.patch(`/claim-adjustments/${op.id}`, op.body)
        } else if (op.kind === 'adj-delete') {
          await api.delete(`/claim-adjustments/${op.id}`)
        }
      } catch (e) {
        setError({
          message: e?.response?.data?.detail || e.message || 'Save failed',
          completed: i,
          total: ops.length,
          failedOp: op,
        })
        setSaving(false)
        queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
        return { ok: false, completed: i, total: ops.length }
      }
    }

    setSaving(false)
    setStep(null)
    queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
    return { ok: true, completed: ops.length, total: ops.length }
  }

  function reset() { setError(null); setStep(null) }

  return { save, saving, error, step, reset }
}

function _adjBody(a) {
  const out = {
    group_code: a.group_code,
    reason_code: a.reason_code,
    amount: a.amount,
    reason_description: a.reason_description,
  }
  if (a.quantity !== undefined && a.quantity !== null && a.quantity !== '') {
    out.quantity = a.quantity
  }
  return out
}
