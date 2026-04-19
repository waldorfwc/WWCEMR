import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, FileText, Users, AlertTriangle,
  Mail, Upload, Shield, Activity, BarChart2, FolderOpen, LogOut,
} from 'lucide-react'

const nav = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/ar', label: 'A/R Dashboard', icon: BarChart2 },
  { to: '/documents', label: 'View Chart', icon: FolderOpen },
  { to: '/claims', label: 'Claims', icon: FileText },
  { to: '/denials', label: 'Denials', icon: AlertTriangle },
  { to: '/appeals', label: 'Appeal Letters', icon: Mail },
  { to: '/import', label: 'Import Files', icon: Upload },
  { to: '/audit', label: 'HIPAA Audit Log', icon: Shield },
]

export default function Sidebar({ user, onLogout }) {
  return (
    <aside className="w-60 bg-primary-500 text-white flex flex-col min-h-screen">
      <div className="px-5 py-5 border-b border-primary-600">
        <div className="flex items-center gap-2">
          <Activity size={22} className="text-blue-200" />
          <div>
            <div className="font-bold text-sm leading-tight">GW Migration</div>
            <div className="text-blue-200 text-xs">System</div>
          </div>
        </div>
        <div className="text-blue-300 text-xs mt-1">Maryland · Internal Use</div>
      </div>

      <nav className="flex-1 py-4">
        {nav.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-5 py-2.5 text-sm transition-colors ${
                isActive
                  ? 'bg-primary-600 text-white font-medium border-r-2 border-blue-200'
                  : 'text-blue-100 hover:bg-primary-600 hover:text-white'
              }`
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}
      </nav>

      {user && (
        <div className="px-5 py-3 border-t border-primary-600">
          <div className="flex items-center gap-2">
            {user.picture ? (
              <img src={user.picture} alt="" className="w-7 h-7 rounded-full" />
            ) : (
              <div className="w-7 h-7 rounded-full bg-primary-400 flex items-center justify-center text-xs font-bold">
                {(user.name || user.email || '?')[0].toUpperCase()}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium truncate">{user.name || user.email}</div>
              <div className="text-[10px] text-blue-300 truncate">{user.email}</div>
            </div>
            <button
              onClick={onLogout}
              className="p-1 hover:bg-primary-400 rounded text-blue-300 hover:text-white"
              title="Sign out"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      )}

      <div className="px-5 py-3 border-t border-primary-600 text-xs text-blue-300">
        <div>Maryland Insurance Article</div>
        <div>§15-1005 Prompt Payment</div>
      </div>
    </aside>
  )
}
