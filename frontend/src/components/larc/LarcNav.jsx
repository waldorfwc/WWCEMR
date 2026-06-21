import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Plus, PackageCheck } from 'lucide-react'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'
import StartLarcProcessDrawer from './StartLarcProcessDrawer'
import CheckoutDeviceDrawer from './CheckoutDeviceDrawer'


// Page links rendered on every /larc page. Each carries the minimum tier
// required to see it (mirrors the route gate). `end` is used for Overview so
// it isn't highlighted on every sub-route.
//
// NB: built INSIDE the component (not a module-level const). MODULE/TIER are
// exported by routes.jsx, which imports this file — a circular dependency.
// Referencing TIER.* at module-init time would read `undefined` mid-cycle and
// throw, crashing the whole app. Reading them at render time is safe (the
// cycle is resolved by then) — same pattern the surgery files use.
function navItems() {
  return [
    { to: '/larc',                 label: 'Overview',        tier: TIER.VIEW, end: true },
    { to: '/larc/devices',         label: 'Devices',         tier: TIER.VIEW },
    { to: '/larc/checkouts',       label: 'Checkouts',       tier: TIER.VIEW },
    { to: '/larc/to-bill',         label: 'To Bill',         tier: TIER.VIEW },
    { to: '/larc/owed',            label: 'Owed',            tier: TIER.VIEW },
    { to: '/larc/reports',         label: 'Reports',         tier: TIER.VIEW },
    { to: '/larc/inventory-count', label: 'Inventory Count', tier: TIER.WORK },
    { to: '/larc/eod',             label: 'EOD Report',      tier: TIER.VIEW },
    { to: '/larc/audit',           label: 'Audit',           tier: TIER.MANAGE },
    { to: '/larc/manual',          label: 'Manual',          tier: TIER.VIEW },
    { to: '/larc/settings',        label: 'Settings',        tier: TIER.MANAGE },
  ]
}


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


export default function LarcNav() {
  const { tier } = useCurrentUser()
  const items = navItems().filter(it => tier(MODULE.LARC, it.tier))
  const [startOpen, setStartOpen] = useState(false)
  const [checkoutOpen, setCheckoutOpen] = useState(false)
  const navigate = useNavigate()

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
        {tier(MODULE.LARC, TIER.WORK) && (
          <div className="pb-1.5 flex items-center gap-2">
            <button className="btn-primary text-sm flex items-center gap-1"
                    onClick={() => setStartOpen(true)}>
              <Plus size={13} /> Start LARC Process
            </button>
            <NavLink to="/larc/devices?add=1"
                     className="btn-secondary text-sm flex items-center gap-1">
              + Add Device
            </NavLink>
            <button className="btn-secondary text-sm flex items-center gap-1"
                    onClick={() => setCheckoutOpen(true)}>
              <PackageCheck size={13} /> Check Out a Device
            </button>
          </div>
        )}
      </div>

      <Outlet />

      {startOpen && <StartLarcProcessDrawer
        onClose={() => setStartOpen(false)}
        onCreated={(id) => { setStartOpen(false); navigate('/larc/assignments/' + id) }} />}

      {checkoutOpen && <CheckoutDeviceDrawer onClose={() => setCheckoutOpen(false)} />}
    </div>
  )
}
