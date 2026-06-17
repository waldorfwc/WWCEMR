import { useEffect } from 'react'
import { Outlet, Navigate, Link, useLocation, useNavigate } from 'react-router-dom'
import { getPelletSession, setPelletSession, clearPelletSession } from '../../lib/pellet-portal-api'
import logoFull from '../../assets/wwc-logo-full.png'

export default function PelletPortalShell() {
  // Capture a staff preview token from the query param on first load.
  const staffToken = new URLSearchParams(window.location.search).get('staff_token')
  if (staffToken) {
    setPelletSession({ token: staffToken })
  }
  useEffect(() => {
    if (staffToken) {
      const params = new URLSearchParams(window.location.search)
      params.delete('staff_token')
      const qs = params.toString()
      window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
    }
  }, [staffToken])

  const token = getPelletSession().token || staffToken
  const loc = useLocation()
  const nav = useNavigate()

  if (!token) {
    return <Navigate to="/pellet-portal/login" replace />
  }

  // "/pellet-portal/home" is the checklist (index); sub-pages are deeper.
  const onChecklist = loc.pathname.replace(/\/+$/, '') === '/pellet-portal/home'

  function signOut() {
    clearPelletSession()
    nav('/pellet-portal/login')
  }

  return (
    <div className="min-h-screen bg-plum-50/40 text-plum-ink">
      <header className="sticky top-0 z-30 bg-white border-b border-plum-100">
        <div className="max-w-3xl mx-auto px-4 md:px-6 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <img src={logoFull} alt="Waldorf Women's Care" className="h-10 w-auto" />
            <div className="text-[11px] uppercase tracking-[0.18em] text-plum-600/70 font-medium">
              Pellet Portal
            </div>
          </div>
          <div className="flex items-center gap-4 shrink-0">
            {!onChecklist && (
              <Link to="/pellet-portal/home"
                    className="text-[12px] text-plum-700 hover:text-plum-900 underline">
                Back to Checklist
              </Link>
            )}
            <button onClick={signOut}
                    className="text-[11px] text-plum-600/70 hover:text-plum-700">
              Sign Out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 md:px-6 py-6 md:py-8">
        <Outlet />
      </main>
    </div>
  )
}
