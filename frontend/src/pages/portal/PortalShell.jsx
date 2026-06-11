import { useEffect, useState } from 'react'
import { Outlet, NavLink, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  CalendarDays, ClipboardCheck, CreditCard, FileText,
  HeartHandshake, MessageSquare, Phone, Menu, X,
} from 'lucide-react'
import { usePortalAuth } from '../../hooks/usePortalAuth'
import { setPortalSession, portalApi } from '../../lib/portal-api'
import PreviewBanner from '../../components/portal/PreviewBanner'
import logoFull from '../../assets/wwc-logo-full.png'

const NAV = [
  { to: '',          label: 'Dashboard',                icon: HeartHandshake, end: true },
  { to: 'payments',  label: 'Payments',                 icon: CreditCard },
  { to: 'schedule',  label: 'Schedule',                 icon: CalendarDays },
  { to: 'consent',   label: 'Consent',                  icon: ClipboardCheck },
  { to: 'documents', label: 'Instructions & Documents', icon: FileText },
  { to: 'messages',  label: 'Messages',                 icon: MessageSquare },
]

export default function PortalShell() {
  const { sid } = useParams()
  const { session, signOut } = usePortalAuth()
  const nav = useNavigate()
  const [mobileOpen, setMobileOpen] = useState(false)

  // Coordinator preview entry: ?staff_token=... bakes the session into
  // localStorage, strips the URL, and reloads so the rest of the shell
  // sees the new token.
  useEffect(() => {
    const url = new URL(window.location.href)
    const tok = url.searchParams.get('staff_token')
    if (tok && sid) {
      setPortalSession({ token: tok, surgery_id: sid })
      url.searchParams.delete('staff_token')
      window.history.replaceState({}, '', url.toString())
      window.location.reload()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Dashboard data drives the sidebar header (patient name, chart,
  // procedure context). Same query used by the Dashboard page — TanStack
  // dedupes the fetch.
  const { data: dash } = useQuery({
    queryKey: ['portal-dashboard', sid],
    queryFn: () => portalApi.get(`/${sid}/dashboard`).then(r => r.data),
    enabled: !!session.token,
    staleTime: 30_000,
  })
  const patient = dash?.surgery

  if (!session.token) {
    nav('/portal/login', { replace: true })
    return null
  }

  function doSignOut() {
    signOut()
    nav('/portal/login')
  }

  const Sidebar = (
    <aside className="w-72 shrink-0 bg-white border-r border-plum-100
                       flex flex-col min-h-screen">
      <div className="px-6 pt-7 pb-6 border-b border-plum-100">
        <img src={logoFull} alt="Waldorf Women's Care · WWC Gynecology &amp; Aesthetics"
             className="h-16 w-auto" />
        <div className="text-[11px] uppercase tracking-[0.18em] text-plum-600/70 font-medium mt-3">
          Surgery Portal
        </div>
      </div>

      {patient && (
        <div className="px-6 py-5 border-b border-plum-100">
          <div className="text-[11px] uppercase tracking-[0.16em] text-plum-600/70 mb-1">
            Care plan for
          </div>
          <div className="font-serif text-[15px] text-plum-ink leading-tight font-semibold">
            {patient.patient_name?.split(',').reverse().join(' ').trim() || 'Your care'}
          </div>
          {patient.chart_number && (
            <div className="text-[11px] text-plum-600/80 mt-1 font-mono">
              chart #{patient.chart_number}
            </div>
          )}
          {patient.procedure && (
            (() => {
              const procs = patient.procedure.includes('; ')
                ? patient.procedure.split('; ').filter(Boolean)
                : [patient.procedure]
              return procs.length > 1 ? (
                <ul className="text-[11px] text-plum-700 mt-3 space-y-0.5 list-disc list-inside marker:text-plum-400">
                  {procs.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              ) : (
                <div className="text-[11px] text-plum-700 mt-3">
                  {procs[0]}
                </div>
              )
            })()
          )}
          {patient.facility && (
            <div className="text-[11px] text-plum-600/80 mt-0.5">
              {patient.facility}
            </div>
          )}
        </div>
      )}

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {NAV.map(item => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.to}
              to={`/portal/s/${sid}${item.to ? '/' + item.to : ''}`}
              end={item.end}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] transition-colors ${
                  isActive
                    ? 'bg-plum-100 text-plum-ink font-medium'
                    : 'text-plum-700/80 hover:bg-plum-50'
                }`
              }>
              {({ isActive }) => (
                <>
                  <Icon size={16}
                        className={isActive ? 'text-plum-700' : 'text-plum-400'} />
                  <span className="flex-1">{item.label}</span>
                </>
              )}
            </NavLink>
          )
        })}
      </nav>

      <div className="px-6 pb-6">
        <div className="rounded-lg bg-plum-50 border border-plum-100 p-3">
          <div className="flex items-center gap-2 text-[11px] text-plum-700">
            <Phone size={12} />
            <span className="font-semibold">Need help?</span>
          </div>
          <div className="text-[11px] text-plum-600/80 mt-1 leading-relaxed">
            Call our office at <strong className="text-plum-ink">240-252-2140</strong>
            {' '}for any questions about your care plan.
          </div>
        </div>
        <div className="text-[10px] text-plum-600/70 mt-3 italic leading-snug px-1">
          Surgery portal access ends <strong className="text-plum-ink">30 days after your surgery date</strong>.
        </div>
        <button onClick={doSignOut}
                className="w-full mt-3 text-[11px] text-plum-600/70 hover:text-plum-700">
          Sign out
        </button>
      </div>
    </aside>
  )

  return (
    <div className="min-h-screen bg-plum-50/40 text-plum-ink">
      <PreviewBanner />

      {/* Mobile top bar */}
      <header className="md:hidden sticky top-0 z-30 bg-white border-b border-plum-100 px-4 py-2 flex items-center justify-between">
        <img src={logoFull} alt="Waldorf Women's Care" className="h-10 w-auto" />
        <button onClick={() => setMobileOpen(true)}
                className="p-2 text-plum-700 hover:bg-plum-50 rounded">
          <Menu size={20} />
        </button>
      </header>

      <div className="flex">
        {/* Desktop sidebar */}
        <div className="hidden md:block">{Sidebar}</div>

        {/* Mobile sidebar drawer */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMobileOpen(false)}>
            <div className="absolute inset-0 bg-plum-900/40" />
            <div className="relative w-72 h-full bg-white shadow-xl"
                 onClick={e => e.stopPropagation()}>
              <button onClick={() => setMobileOpen(false)}
                      className="absolute top-3 right-3 text-plum-600 hover:text-plum-900 p-1">
                <X size={18} />
              </button>
              {Sidebar}
            </div>
          </div>
        )}

        <main className="flex-1 min-h-screen min-w-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
