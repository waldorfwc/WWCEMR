import { fmt } from '../utils/api'

const STYLES = {
  queued:    'bg-plum-100 text-plum-700',
  sent:      'bg-plum-100 text-plum-700',
  delivered: 'bg-green-100 text-green-800',
  failed:    'bg-red-100 text-red-800',
}

export default function FaxStatusChip({ row, onRetry }) {
  if (!row) return null
  const style = STYLES[row.status] || 'bg-gray-100 text-gray-600'
  const label = fmt.faxStatus(row.status)

  const content = (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${style}`}>
      {label}
      {row.status === 'delivered' && row.delivered_at && (
        <span className="ml-1 opacity-75">· {fmt.time(row.delivered_at)}</span>
      )}
      {row.status === 'sent' && row.sent_at && (
        <span className="ml-1 opacity-75">· {fmt.time(row.sent_at)}</span>
      )}
    </span>
  )

  if (row.status === 'failed' && onRetry) {
    return (
      <button
        onClick={() => onRetry(row)}
        title={row.error || 'Retry'}
        className="inline-flex items-center gap-1.5"
      >
        {content}
        <span className="text-[11px] text-plum-700 underline">retry</span>
      </button>
    )
  }
  return content
}
