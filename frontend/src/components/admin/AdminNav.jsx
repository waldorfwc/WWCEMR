import { NavLink, Outlet } from 'react-router-dom'


// Shared top-nav rendered on every /admin page. The whole /admin section is
// super-admin gated at the route level, so the items need no per-item gate.
// Admin is reached from the username menu (TopNav) — intentionally NOT a
// sidebar entry — so this nav is the in-section navigation.
//
// NB: no MODULE/TIER imports here (admin is super-admin, not module/tier
// gated), so there's no routes.jsx circular-import concern.
function navItems() {
  return [
    { to: '/admin',             label: 'Users',     end: true },
    { to: '/admin/permissions', label: 'Permissions' },
    { to: '/admin/templates',   label: 'Templates' },
  ]
}


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


export default function AdminNav() {
  return (
    <div>
      <div className="mb-4 flex items-center justify-between border-b border-border-subtle">
        <nav className="flex gap-0.5">
          {navItems().map(it => (
            <NavLink key={it.to} to={it.to} end={it.end} className={navClass}>
              {it.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet />
    </div>
  )
}
