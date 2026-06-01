import { useEffect } from 'react'
import { Outlet, Link, useNavigate, useParams } from 'react-router-dom'
import { usePortalAuth } from '../../hooks/usePortalAuth'
import { setPortalSession } from '../../lib/portal-api'

const NAV = [
  { to: '',          label: 'Dashboard' },
  { to: 'payments',  label: 'Payments' },
  { to: 'schedule',  label: 'Schedule' },
  { to: 'consent',   label: 'Consent' },
  { to: 'documents', label: 'Documents' },
  { to: 'messages',  label: 'Messages',  comingSoon: true },
]

export default function PortalShell() {
  const { sid } = useParams()
  const { session, signOut } = usePortalAuth()
  const nav = useNavigate()

  // Coordinator preview entry: if URL has ?staff_token=..., bake it into
  // localStorage as the active session, then strip from the URL so it
  // doesn't show up in copy/paste or browser history. Runs once on mount.
  useEffect(() => {
    const url = new URL(window.location.href)
    const tok = url.searchParams.get('staff_token')
    if (tok && sid) {
      setPortalSession({ token: tok, surgery_id: sid })
      url.searchParams.delete('staff_token')
      window.history.replaceState({}, '', url.toString())
      // Force a re-render so the session check below sees the new token.
      // Easiest path: reload. The page is fresh anyway since this is the
      // staff member's first visit.
      window.location.reload()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!session.token) {
    nav('/portal/login', { replace: true })
    return null
  }
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <div className="text-lg font-semibold text-plum-700">WWC Apps</div>
        <button className="text-sm text-gray-600 underline"
                onClick={() => { signOut(); nav('/portal/login') }}>
          Sign out
        </button>
      </header>
      <div className="flex">
        <nav className="w-48 border-r border-gray-200 bg-white p-3 hidden sm:block">
          {NAV.map(item => (
            <Link key={item.to}
                  to={`/portal/s/${sid}/${item.to}`}
                  className={`block px-2 py-2 rounded text-sm ${item.comingSoon ? 'text-gray-400' : 'text-gray-800 hover:bg-gray-100'}`}>
              {item.label}{item.comingSoon ? ' · soon' : ''}
            </Link>
          ))}
        </nav>
        <main className="flex-1 p-4 max-w-3xl mx-auto"><Outlet /></main>
      </div>
    </div>
  )
}
