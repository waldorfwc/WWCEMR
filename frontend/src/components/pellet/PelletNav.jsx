import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import api from '../../utils/api'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'


// Page links rendered on every /pellets page. Each carries the minimum tier
// required to see it (mirrors the route gate). `end` is used for Patients so
// it isn't highlighted on every sub-route. Dose Types lives under Settings,
// so it isn't a top-level bar entry.
//
// NB: built INSIDE the component (not a module-level const). MODULE/TIER are
// exported by routes.jsx, which imports this file — a circular dependency.
// Referencing TIER.* at module-init time would read `undefined` mid-cycle and
// throw, crashing the whole app. Reading them at render time is safe (the
// cycle is resolved by then) — same pattern the surgery/larc files use.
function navItems() {
  return [
    { to: '/pellets',           label: 'Patients',  tier: TIER.VIEW, end: true },
    { to: '/pellets/activity',  label: 'Patient Activity', tier: TIER.VIEW, badge: 'activity' },
    { to: '/pellets/inventory', label: 'Inventory', tier: TIER.VIEW },
    { to: '/pellets/counts',    label: 'Counts',    tier: TIER.WORK },
    { to: '/pellets/audit',     label: 'Audit',     tier: TIER.VIEW },
    { to: '/pellets/reports',   label: 'Reports',   tier: TIER.VIEW },
    { to: '/pellets/recall',    label: 'Recall',    tier: TIER.WORK },
    { to: '/pellets/manual',    label: 'Manual',    tier: TIER.VIEW },
    { to: '/pellets/schedule',  label: 'Scheduling', tier: TIER.MANAGE },
    { to: '/pellets/settings',  label: 'Settings',  tier: TIER.MANAGE },
  ]
}


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


function PelletActivityBadge() {
  const { data } = useQuery({
    queryKey: ['pellet-activity-unread'],
    queryFn: () => api.get('/pellets/activity/unread-count').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const count = data?.count || 0
  if (!count) return null
  return (
    <span className="ml-1 bg-red-500 text-white text-[10px] rounded-full px-1.5 py-0.5 font-semibold">
      {count}
    </span>
  )
}


export default function PelletNav() {
  const { tier } = useCurrentUser()
  const items = navItems().filter(it => tier(MODULE.PELLETS, it.tier))

  return (
    <div>
      {/* No right-side action: the primary create flows (Enroll patient,
          Receive shipment, etc.) are all in-page drawers with no URL, so we
          leave creation to the landing pages rather than fake-wiring a drawer
          into the nav. */}
      <div className="mb-4 flex items-center justify-between border-b border-border-subtle">
        <nav className="flex gap-0.5">
          {items.map(it => (
            <NavLink key={it.to} to={it.to} end={it.end} className={navClass}>
              {it.label}
              {it.badge === 'activity' && <PelletActivityBadge />}
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet />
    </div>
  )
}
