import { NavLink, Outlet } from 'react-router-dom'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'


// Page links rendered on every /checklist page. My Checklist + Settings are
// ungated (any authed user). Manager Dashboard + Templates link OUT to routes
// that live OUTSIDE this layout — clicking them navigates away (those pages
// keep their own layout; the checklist nav won't render there). They carry
// their own gate so they only show for users who can reach them.
//
// NB: built INSIDE the component (not a module-level const). MODULE/TIER are
// exported by routes.jsx, which imports this file — a circular dependency.
// Referencing TIER.* / MODULE.* at module-init time would read `undefined`
// mid-cycle and throw, crashing the whole app. Reading them at render time is
// safe (the cycle is resolved by then) — same pattern the pellet/surgery files
// use.
function navItems() {
  return [
    { to: '/checklist',          label: 'My Checklist',      end: true },                // always
    { to: '/manager-dashboard',  label: 'Manager Dashboard', tier: TIER.MANAGE },        // module tier gate
    { to: '/admin/templates',    label: 'Templates',         superAdmin: true },         // super-admin gate
    { to: '/checklist/settings', label: 'Settings' },                                    // always
  ]
}


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


export default function ChecklistNav() {
  const { tier, isSuperAdmin } = useCurrentUser()
  const items = navItems().filter(it =>
    it.superAdmin
      ? isSuperAdmin
      : it.tier
        ? tier(MODULE.MY_CHECKLIST, it.tier)
        : true
  )

  return (
    <div>
      <div className="mb-4 flex items-center justify-between border-b border-border-subtle">
        <nav className="flex gap-0.5">
          {items.map(it => (
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
