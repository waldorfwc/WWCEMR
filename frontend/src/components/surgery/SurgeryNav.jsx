import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import api from '../../utils/api'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'
import SurgeryAddMenu from './SurgeryAddMenu'


// Page links rendered on every /surgery page. Each carries the minimum tier
// required to see it (mirrors the route gate). `end` is used for Overview so
// it isn't highlighted on every sub-route.
const NAV_ITEMS = [
  { to: '/surgery',                label: 'Overview',       tier: TIER.VIEW,   end: true },
  { to: '/surgery/calendar',       label: 'Calendar',       tier: TIER.VIEW },
  { to: '/surgery/block-schedule', label: 'Block Schedule', tier: TIER.MANAGE },
  { to: '/surgery/waitlist',       label: 'Waitlist',       tier: TIER.WORK },
  { to: '/surgery/fee-schedule',   label: 'Fee Schedule',   tier: TIER.MANAGE },
  { to: '/surgery/messages',       label: 'Messages',       tier: TIER.WORK, badge: true },
  { to: '/surgery/settings',       label: 'Settings',       tier: TIER.MANAGE },
]


function navClass({ isActive }) {
  return `px-2.5 py-2 -mb-px border-b-2 text-[13px] whitespace-nowrap transition-colors ${
    isActive
      ? 'text-plum-700 border-plum-700 font-medium'
      : 'text-muted border-transparent hover:text-plum-700'
  }`
}


function MessagesBadge() {
  const { data } = useQuery({
    queryKey: ['staff-inbox'],
    queryFn: () => api.get('/staff/messages/inbox').then(r => r.data),
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


export default function SurgeryNav() {
  const { tier } = useCurrentUser()
  const items = NAV_ITEMS.filter(it => tier(MODULE.SURGERY, it.tier))

  return (
    <div>
      <div className="mb-4 flex items-center justify-between border-b border-border-subtle">
        <nav className="flex gap-0.5">
          {items.map(it => (
            <NavLink key={it.to} to={it.to} end={it.end} className={navClass}>
              {it.label}
              {it.badge && <MessagesBadge />}
            </NavLink>
          ))}
        </nav>
        <div className="pb-1.5">
          <SurgeryAddMenu />
        </div>
      </div>

      <Outlet />
    </div>
  )
}
