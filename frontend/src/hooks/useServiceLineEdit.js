import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Orchestrates the save sequence for a service-line drawer.
 *
 * save({ claimId, lineId, fields, adjustments }):
 *  - If lineId is null, POST /claims/{claimId}/service-lines with all fields,
 *    then POST each 'new' SL adjustment against the returned id.
 *  - Else PATCH /service-lines/{lineId} if fields changed, then
 *    POST/PATCH/DELETE SL adjustments in that order.
 *
 * del({ claimId, lineId }): DELETE /service-lines/{lineId}
 */
export function useServiceLineEdit() {
  const queryClient = useQueryClient()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [step, setStep] = useState(null)

  async function save({ claimId, lineId, fields, fieldsDiff, adjustments }) {
    setSaving(true); setError(null)
    try {
      if (lineId == null) {
        // Add mode — POST line, then POST its adjustments
        setStep('1/?')
        const r = await api.post(`/claims/${claimId}/service-lines`, fields || {})
        const newId = r.data.id
        const newAdj = adjustments.filter(a => a.op === 'new')
        for (let i = 0; i < newAdj.length; i++) {
          setStep(`${i + 2}/${newAdj.length + 1}`)
          await api.post(`/service-lines/${newId}/adjustments`, _adjBody(newAdj[i]))
        }
      } else {
        const ops = []
        if (fieldsDiff && Object.keys(fieldsDiff).length > 0) {
          ops.push({ kind: 'sl-patch', body: fieldsDiff })
        }
        for (const a of adjustments) {
          if (a.op === 'new') ops.push({ kind: 'sla-post', body: _adjBody(a) })
          else if (a.op === 'edited') ops.push({ kind: 'sla-patch', id: a.id, body: _adjBody(a) })
          else if (a.op === 'deleted') ops.push({ kind: 'sla-delete', id: a.id })
        }
        for (let i = 0; i < ops.length; i++) {
          setStep(`${i + 1}/${ops.length}`)
          const op = ops[i]
          if (op.kind === 'sl-patch') await api.patch(`/service-lines/${lineId}`, op.body)
          else if (op.kind === 'sla-post') await api.post(`/service-lines/${lineId}/adjustments`, op.body)
          else if (op.kind === 'sla-patch') await api.patch(`/service-line-adjustments/${op.id}`, op.body)
          else if (op.kind === 'sla-delete') await api.delete(`/service-line-adjustments/${op.id}`)
        }
      }
      setSaving(false); setStep(null)
      queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
      return { ok: true }
    } catch (e) {
      setError({ message: e?.response?.data?.detail || e.message || 'Save failed' })
      setSaving(false)
      queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
      return { ok: false }
    }
  }

  async function del({ claimId, lineId }) {
    setSaving(true); setError(null)
    try {
      await api.delete(`/service-lines/${lineId}`)
      setSaving(false)
      queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
      return { ok: true }
    } catch (e) {
      setError({ message: e?.response?.data?.detail || e.message || 'Delete failed' })
      setSaving(false)
      return { ok: false }
    }
  }

  function reset() { setError(null); setStep(null) }

  return { save, del, saving, error, step, reset }
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
