import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, Pencil, Plus, RotateCcw, Trash2, Users } from 'lucide-react'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'
import { ManualCreateDrawer, UpdateSurgeryDrawer, DeleteSurgeryDrawer } from './surgeryDrawers'


/**
 * "Add ▾" dropdown shown on every /surgery page (right side of SurgeryNav).
 * Gated on surgery:WORK — returns null for view-only users. Items open the
 * shared drawers (New Surgery / Update Surgery) or link to bulk import.
 */
export default function SurgeryAddMenu() {
  const { tier } = useCurrentUser()
  const [open, setOpen] = useState(false)
  const [showManual, setShowManual] = useState(false)
  const [showUpdate, setShowUpdate] = useState(false)
  const [showDelete, setShowDelete] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    function handleEsc(e) { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [open])

  if (!tier(MODULE.SURGERY, TIER.WORK)) return null

  return (
    <>
      <div className="relative" ref={ref}>
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="btn-primary text-sm flex items-center gap-1"
        >
          <Plus size={13} /> Add
          <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>

        {open && (
          <div className="absolute right-0 mt-1 w-56 bg-white border border-border-subtle rounded-md shadow-lg py-1 z-20">
            <button
              type="button"
              onClick={() => { setOpen(false); setShowManual(true) }}
              className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
            >
              <Plus size={14} className="text-plum-600" /> Add New Surgery
            </button>
            <button
              type="button"
              onClick={() => { setOpen(false); setShowUpdate(true) }}
              className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
            >
              <Pencil size={14} className="text-plum-600" /> Update Surgery
            </button>
            <Link
              to="/surgery/bulk-import"
              onClick={() => setOpen(false)}
              className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
            >
              <Users size={14} className="text-plum-600" /> Upload Demographics
            </Link>
            {tier(MODULE.SURGERY, TIER.MANAGE) && (
              <>
                <div className="my-1 border-t border-border-subtle" />
                <button
                  type="button"
                  onClick={() => { setOpen(false); setShowDelete(true) }}
                  className="w-full px-3 py-2 text-left text-sm text-red-700 hover:bg-red-50 flex items-center gap-2"
                >
                  <Trash2 size={14} /> Delete Surgery
                </button>
                <Link
                  to="/surgery/deleted"
                  onClick={() => setOpen(false)}
                  className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
                >
                  <RotateCcw size={14} className="text-plum-600" /> Restore Deleted
                </Link>
              </>
            )}
          </div>
        )}
      </div>

      {showManual && <ManualCreateDrawer onClose={() => setShowManual(false)} />}
      {showUpdate && <UpdateSurgeryDrawer onClose={() => setShowUpdate(false)} />}
      {showDelete && <DeleteSurgeryDrawer onClose={() => setShowDelete(false)} />}
    </>
  )
}
