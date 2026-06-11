import { Loader2 } from 'lucide-react'

// Canonical loading-state primitive. Mirrors EmptyState's API so loading
// and empty UIs share visual treatment (no font-weight or color drift).
//
// Sizes:
//   compact — inline or table-cell.
//   default — page-level / centered card body.
//
// Usage:
//   {isLoading && <LoadingState />}
//   <LoadingState compact text="Loading payers…" />
export default function LoadingState({ text = 'Loading…', compact = false }) {
  const wrap = compact
    ? 'flex items-center justify-center gap-2 text-[12px] text-muted py-3'
    : 'flex items-center justify-center gap-2 text-sm text-muted py-10'
  const iconSize = compact ? 14 : 18
  return (
    <div className={wrap}>
      <Loader2 size={iconSize} className="animate-spin text-plum-400" />
      <span>{text}</span>
    </div>
  )
}
