import { NavLink } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import logoMark from '../../assets/wwc-logo.png'
import { useCurrentUser } from '../../hooks/useCurrentUser'

const nav = [
  { to: '/',          label: 'Dashboard' },
  { to: '/documents', label: 'Charts' },
  { to: '/ar',        label: 'A/R' },
  { to: '/claims',    label: 'Claims' },
  { to: '/denials',   label: 'Denials' },
  { to: '/appeals',   label: 'Appeals' },
  { to: '/import',    label: 'Import' },
  { to: '/audit',     label: 'Audit' },
]

const CLINICAL_NAV = [
  { to: '/documents', label: 'Charts' },
]

const ADMIN_NAV_ENTRY = { to: '/admin', label: 'Admin' }

export default function TopNav({ user, onLogout }) {
  const { isAdmin, isClinical } = useCurrentUser()
  const visibleNav = isClinical
    ? CLINICAL_NAV
    : (isAdmin ? [...nav, ADMIN_NAV_ENTRY] : nav)

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
        <span className="bg-plum-100 text-plum-700 px-2.5 py-1 rounded text-[11px] font-medium">
          Maryland · Internal
        </span>
        {user && (
          <div className="flex items-center gap-2">
            {user.picture ? (
              <img src={user.picture} alt="" className="w-8 h-8 rounded-full" />
            ) : (
              <div className="w-8 h-8 rounded-full bg-plum-300 text-plum-ink flex items-center justify-center text-xs font-semibold">
                {(user.name || user.email || '?')[0].toUpperCase()}
              </div>
            )}
            <div className="text-[12px] leading-tight">
              <div className="font-medium text-ink truncate max-w-[160px]">
                {user.name || user.email}
              </div>
              <div className="text-muted truncate max-w-[160px]">{user.email}</div>
            </div>
            <button
              onClick={onLogout}
              className="p-1.5 rounded hover:bg-plum-100 text-muted hover:text-plum-700"
              title="Sign out"
            >
              <LogOut size={16} />
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
