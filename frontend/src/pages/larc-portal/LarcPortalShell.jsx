import { useEffect } from 'react'
import { Outlet, Navigate, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getLarcSession, setLarcSession, clearLarcSession, larcPortalApi } from '../../lib/larc-portal-api'
import logoFull from '../../assets/wwc-logo-full.png'

const NAV_LINKS = [
  { to: '/larc-portal/home',            label: 'Status', end: true },
  { to: '/larc-portal/home/payments',   label: 'Payments' },
  { to: '/larc-portal/home/enrollment', label: 'Enrollment' },
  { to: '/larc-portal/home/documents',  label: 'Documents' },
]

export default function LarcPortalShell() {
  // Capture a staff preview token from the query param on first load.
  const staffToken = new URLSearchParams(window.location.search).get('staff_token')
  if (staffToken) {
    setLarcSession({ token: staffToken })
  }
  useEffect(() => {
    if (staffToken) {
      const params = new URLSearchParams(window.location.search)
      params.delete('staff_token')
      const qs = params.toString()
      window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
    }
  }, [staffToken])

  const token = getLarcSession().token || staffToken
  const nav = useNavigate()
  useLocation() // keep NavLink active state in sync across navigations

  const dashQ = useQuery({
    queryKey: ['larc-portal-dash'],
    queryFn: () => larcPortalApi.get('/dashboard').then(r => r.data),
    enabled: !!token,
    staleTime: 30_000,
  })

  if (!token) {
    return <Navigate to="/larc-portal/login" replace />
  }

  const patient = dashQ.data?.patient

  function signOut() {
    clearLarcSession()
    nav('/larc-portal/login')
  }

  return (
    <div className="min-h-screen bg-plum-50/40 text-plum-ink">
      <header className="sticky top-0 z-30 bg-white border-b border-plum-100">
        <div className="max-w-5xl mx-auto px-4 md:px-6 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <img src={logoFull} alt="Waldorf Women's Care" className="h-10 w-auto" />
            <div className="text-[11px] uppercase tracking-[0.18em] text-plum-600/70 font-medium">
              Device Tracking
            </div>
          </div>
          <div className="flex items-center gap-4 shrink-0">
            <button onClick={signOut}
                    className="text-[11px] text-plum-600/70 hover:text-plum-700">
              Sign Out
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-4 md:px-6 py-6 md:py-8 flex flex-col md:flex-row gap-6">
        <nav className="w-full md:w-56 shrink-0">
          <div className="bg-white rounded-2xl border border-plum-100 shadow-sm p-4 mb-3">
            <div className="text-[15px] text-plum-ink font-semibold leading-tight">
              {patient?.patient_name || '—'}
            </div>
            {patient?.chart_number && (
              <div className="text-[12px] text-plum-700/70 mt-0.5">
                MRN {patient.chart_number}
              </div>
            )}
          </div>
          <div className="bg-white rounded-2xl border border-plum-100 shadow-sm p-2 flex flex-row md:flex-col gap-1 overflow-x-auto">
            {NAV_LINKS.map(link => (
              <NavLink key={link.to} to={link.to} end={link.end}
                       className={({ isActive }) =>
                         `px-3 py-2 rounded-lg text-[13px] font-medium whitespace-nowrap transition ${
                           isActive
                             ? 'bg-plum-700 text-white'
                             : 'text-plum-700 hover:bg-plum-50'}`}>
                {link.label}
              </NavLink>
            ))}
          </div>
        </nav>

        <main className="flex-1 min-w-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
