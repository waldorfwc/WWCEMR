import { useQuery } from '@tanstack/react-query'
import { X, AlertTriangle } from 'lucide-react'
import api from '../../utils/api'

export default function EnrollmentPreviewModal({ assignmentId, onClose }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['larc-enrollment-preview', assignmentId],
    queryFn: () => api.get(`/larc/assignments/${assignmentId}/enrollment/preview`)
      .then(r => r.data),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
         onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl w-[460px] max-h-[80vh] overflow-auto p-4"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">Enrollment Form Preview</h3>
          <button onClick={onClose}><X size={16} /></button>
        </div>
        {isLoading && <div className="text-[12px] text-gray-500">Loading…</div>}
        {error && <div className="text-[12px] text-danger">Couldn't load preview.</div>}
        {data && (
          <>
            {data.blanks?.length > 0 && (
              <div className="flex items-start gap-1.5 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mb-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>{data.blanks.length} field{data.blanks.length > 1 ? 's' : ''} will
                  send blank: {data.blanks.join(', ')}.</span>
              </div>
            )}
            {!data.sendable && (
              <div className="text-[11px] text-danger mb-2">
                Patient email is required before sending.
              </div>
            )}
            <table className="w-full text-[12px]">
              <tbody>
                {data.fields.map(f => (
                  <tr key={f.label} className="border-b border-border-subtle">
                    <td className="py-1 pr-2 text-gray-500 align-top w-[45%]">{f.label}</td>
                    <td className={'py-1 ' + (f.blank ? 'text-amber-700 italic' : 'text-gray-900')}>
                      {f.blank ? '— blank —' : f.value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}
