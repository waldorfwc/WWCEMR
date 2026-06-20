/**
 * HelpButton — a small round "?" that opens the page's Help slide-over.
 *
 * Route-aware: reads the current pathname, resolves it to a HELP_CONTENT
 * entry, and renders nothing when no help is authored for that route. Mount
 * it ONCE in the app shell (TopNav) — it appears automatically only on pages
 * that have help.
 */
import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { HelpCircle } from 'lucide-react'
import { HELP_CONTENT, helpKeyForPath } from './helpContent'
import HelpPanel from './HelpPanel'

export default function HelpButton() {
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)

  const key = helpKeyForPath(pathname)
  const content = key ? HELP_CONTENT[key] : null
  if (!content) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={`Help · ${content.title}`}
        aria-label={`Help for ${content.title}`}
        className="w-8 h-8 rounded-full flex items-center justify-center text-plum-600 hover:text-plum-700 hover:bg-plum-50 transition-colors"
      >
        <HelpCircle size={20} />
      </button>
      {open && <HelpPanel content={content} onClose={() => setOpen(false)} />}
    </>
  )
}
