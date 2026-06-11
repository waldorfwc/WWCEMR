import { Inbox } from 'lucide-react'

// Canonical empty-state primitive. Replaces the "No X yet." muted-italic
// one-liners scattered across pages — centers an icon, headline, optional
// body, and optional CTA. The right place for a small bit of visual
// interest (Fable UX critique).
//
// Sizes:
//   compact — inline (table-cell, narrow card). Smaller icon + tighter padding.
//   default — card body / page section. Medium icon + generous padding.
//
// Usage:
//   <EmptyState title="No tasks for today" body="Either you're done or…" />
//   <EmptyState icon={Receipt} title="No claims match these filters" compact />
export default function EmptyState({
  icon: Icon = Inbox,
  title,
  body,
  action,
  compact = false,
}) {
  const wrap = compact
    ? 'flex flex-col items-center text-center py-6 px-3'
    : 'flex flex-col items-center text-center py-10 px-4'
  const iconWrap = compact
    ? 'mb-2 text-plum-300'
    : 'mb-3 text-plum-300'
  const iconSize = compact ? 24 : 36
  const titleCls = compact
    ? 'text-[13px] font-medium text-ink'
    : 'text-sm font-medium text-ink'
  const bodyCls = 'text-[12px] text-muted mt-1 max-w-sm'

  return (
    <div className={wrap}>
      <div className={iconWrap}>
        <Icon size={iconSize} strokeWidth={1.5} />
      </div>
      {title && <div className={titleCls}>{title}</div>}
      {body && <div className={bodyCls}>{body}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
