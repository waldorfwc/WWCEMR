import { NavLink, Outlet } from 'react-router-dom'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'


// Page links rendered on every /recalls page. Each carries the minimum tier
// required to see it (mirrors the route gate). `end` is used for Overview so
// it isn't highlighted on every sub-route.
//
// NB: built INSIDE the component (not a module-level const). MODULE/TIER are
// exported by routes.jsx, which imports this file — a circular dependency.
// Referencing TIER.* at module-init time would read `undefined` mid-cycle and
// throw, crashing the whole app. Reading them at render time is safe (the
// cycle is resolved by then) — same pattern the pellet/surgery/larc files use.
function navItems() {
  return [
    { to: '/recalls',          label: 'Overview', tier: TIER.WORK, end: true },
    { to: '/recalls/settings', label: 'Settings', tier: TIER.MANAGE },
    { to: '/recalls/manual',   label: 'Manual',   tier: TIER.VIEW },
  ]
}


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


export default function RecallNav() {
  const { tier } = useCurrentUser()
  const items = navItems().filter(it => tier(MODULE.RECALL, it.tier))

  return (
    <div>
      {/* No right-side action: recall has no global create flow. */}
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
