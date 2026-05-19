import { useEffect, useRef, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { ChevronDown, History, LogOut, Shield, User as UserIcon } from 'lucide-react'
import logoMark from '../../assets/wwc-logo.png'
import { useCurrentUser } from '../../hooks/useCurrentUser'

const baseNav = [
  { to: '/checklist', label: 'My Checklist' },
  { to: '/documents', label: 'Charts' },
  { to: '/active-ar', label: 'Active AR' },
  { to: '/billing', label: 'Billing' },
  { to: '/billing/code-helper', label: 'Code Helper' },
]

const RECALLS_ENTRY = { to: '/recalls', label: 'Recalls' }
const SURGERY_ENTRY = { to: '/surgery', label: 'Surgery' }
const LARC_ENTRY = { to: '/larc', label: 'Device Tracking' }
const PELLET_ENTRY = { to: '/pellets', label: 'Pellets' }
const MANAGER_ENTRY = { to: '/manager-dashboard', label: 'Manager' }

const CLINICAL_NAV = [
  { to: '/checklist', label: 'My Checklist' },
  { to: '/documents', label: 'Charts' },
]

export default function TopNav({ user, onLogout }) {
  const { isAdmin, isBilling, isClinical, has } = useCurrentUser()
  const canRecall = has?.('recall:work')
  const canSurgery = has?.('surgery:read')
  const canLarc = has?.('larc:read')
  const canPellet = has?.('pellet:read')
  const canManageChecklist = has?.('checklist:manage')
  // Build nav based on permissions
  let visibleNav
  if (isClinical) {
    visibleNav = canRecall ? [...CLINICAL_NAV, RECALLS_ENTRY] : CLINICAL_NAV
  } else {
    visibleNav = canRecall ? [...baseNav, RECALLS_ENTRY] : baseNav
  }
  if (canSurgery) {
    visibleNav = [...visibleNav, SURGERY_ENTRY]
  }
  if (canLarc) {
    visibleNav = [...visibleNav, LARC_ENTRY]
  }
  if (canPellet) {
    visibleNav = [...visibleNav, PELLET_ENTRY]
  }
  if (canManageChecklist) {
    visibleNav = [...visibleNav, MANAGER_ENTRY]
  }
  const canSeeAudit = isAdmin || isBilling

  return (
    <header className="bg-white border-b border-border-subtle h-[60px] px-6 flex items-center gap-6 sticky top-0 z-10">
      <div className="flex items-center gap-2.5 shrink-0">
        <img src={logoMark} alt="WWC" className="w-8 h-8 object-contain" />
        <div className="leading-tight">
          <div className="font-serif font-semibold text-plum-700 text-[12px] tracking-wordmark">
            WWC GYNECOLOGY
          </div>
          <div className="font-serif italic text-plum-600 text-[11px] -mt-0.5">
            &amp; Aesthetics
          </div>
        </div>
      </div>

      <nav className="flex gap-0.5 text-sm">
        {visibleNav.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `px-3 py-2 -mb-px border-b-2 transition-colors ${
                isActive
                  ? 'text-plum-700 border-plum-700 font-medium'
                  : 'text-muted border-transparent hover:text-plum-700'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        {user && <UserMenu user={user} isAdmin={isAdmin} canSeeAudit={canSeeAudit} onLogout={onLogout} />}
      </div>
    </header>
  )
}

function UserMenu({ user, isAdmin, canSeeAudit, onLogout }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
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

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-plum-50 transition-colors"
      >
        {user.picture ? (
          <img src={user.picture} alt="" className="w-8 h-8 rounded-full" />
        ) : (
          <div className="w-8 h-8 rounded-full bg-plum-300 text-plum-ink flex items-center justify-center text-xs font-semibold">
            {(user.name || user.email || '?')[0].toUpperCase()}
          </div>
        )}
        <div className="text-[12px] leading-tight text-left">
          <div className="font-medium text-ink truncate max-w-[160px]">
            {user.name || user.email}
          </div>
          <div className="text-muted truncate max-w-[160px]">{user.email}</div>
        </div>
        <ChevronDown size={14} className={`text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-48 bg-white border border-border-subtle rounded-md shadow-lg py-1 z-20">
          <button
            type="button"
            onClick={() => { setOpen(false); navigate('/me') }}
            className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
          >
            <UserIcon size={14} className="text-plum-600" /> My Profile
          </button>
          {isAdmin && (
            <button
              type="button"
              onClick={() => { setOpen(false); navigate('/admin') }}
              className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
            >
              <Shield size={14} className="text-plum-600" /> Admin
            </button>
          )}
          {canSeeAudit && (
            <button
              type="button"
              onClick={() => { setOpen(false); navigate('/audit') }}
              className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
            >
              <History size={14} className="text-plum-600" /> Audit Log
            </button>
          )}
          <div className="border-t border-gray-100 my-1" />
          <button
            type="button"
            onClick={() => { setOpen(false); onLogout() }}
            className="w-full px-3 py-2 text-left text-sm text-ink hover:bg-plum-50 flex items-center gap-2"
          >
            <LogOut size={14} className="text-muted" /> Sign out
          </button>
        </div>
      )}
    </div>
  )
}
