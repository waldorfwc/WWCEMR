import { NavLink, Outlet } from 'react-router-dom'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'


// Page links rendered on every /marketing page. Each carries the minimum tier
// required to see it (mirrors the route gate). `end` is used for Reviews so
// it isn't highlighted on every sub-route.
//
// NB: built INSIDE the component (not a module-level const). MODULE/TIER are
// exported by routes.jsx, which imports this file — a circular dependency.
// Referencing TIER.* at module-init time would read `undefined` mid-cycle and
// throw, crashing the whole app. Reading them at render time is safe (the
// cycle is resolved by then) — same pattern the surgery/pellet nav files use.
function navItems() {
  return [
    { to: '/marketing',             label: 'Reviews',     tier: TIER.VIEW,   end: true },
    { to: '/marketing/leaderboard', label: 'Leaderboard', tier: TIER.VIEW },
    { to: '/marketing/profiles',    label: 'Profiles',    tier: TIER.MANAGE },
    { to: '/marketing/manual',      label: 'Manual',      tier: TIER.VIEW },
  ]
}


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


export default function MarketingNav() {
  const { tier } = useCurrentUser()
  const items = navItems().filter(it => tier(MODULE.REPUTATION, it.tier))

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
