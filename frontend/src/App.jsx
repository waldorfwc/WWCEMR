import { useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import TopNav from './components/layout/TopNav'
import { LoginPage, AuthCallback } from './pages/Login'
import { useCurrentUser } from './hooks/useCurrentUser'
import PrivateRoute from './components/PrivateRoute'
import { ConfirmProvider } from './components/ui/ConfirmDialog'
import { ROUTES } from './routes.jsx'
import PatientSurgery from './pages/PatientSurgery'
import ProviderMissingChargesPortal from './pages/ProviderMissingChargesPortal'
import PortalLogin from './pages/portal/PortalLogin'
import PortalVerify from './pages/portal/PortalVerify'
import PortalShell from './pages/portal/PortalShell'
import PortalDashboard from './pages/portal/Dashboard'
import Payments from './pages/portal/Payments'
import Schedule from './pages/portal/Schedule'
import Consent from './pages/portal/Consent'
import PortalDocuments from './pages/portal/Documents'
import Messages from './pages/portal/Messages'
import PreviewPortal from './pages/portal/PreviewPortal'
import PelletPortalLogin from './pages/pellet-portal/PelletPortalLogin'
import PelletPortalVerify from './pages/pellet-portal/PelletPortalVerify'
import PelletPortalShell from './pages/pellet-portal/PelletPortalShell'
import PelletDashboard from './pages/pellet-portal/PelletDashboard'
import PelletMammo from './pages/pellet-portal/PelletMammo'
import PelletLabs from './pages/pellet-portal/PelletLabs'
import PelletConsent from './pages/pellet-portal/PelletConsent'
import PelletPayments from './pages/pellet-portal/PelletPayments'
import PelletSchedule from './pages/pellet-portal/PelletSchedule'
import PelletAppointments from './pages/pellet-portal/PelletAppointments'
import PelletReceipts from './pages/pellet-portal/PelletReceipts'
import PelletInfo from './pages/pellet-portal/PelletInfo'
import ReviewForm from './pages/reputation/ReviewForm'
import ReputationEmbed from './pages/reputation/Embed'


/** Wrap an element in <PrivateRoute> only when the route declares a gate. */
function guard(route) {
  if (route.superAdmin || (route.module && route.tier != null)) {
    return (
      <PrivateRoute
        module={route.module}
        tier={route.tier}
        superAdmin={route.superAdmin}
      >
        {route.element}
      </PrivateRoute>
    )
  }
  return route.element
}

/** Render ROUTES (and any nested children) as <Route> elements. */
function renderRoutes(routes) {
  return routes.map(r => (
    <Route key={r.path || 'index'} index={r.index} path={r.path} element={guard(r)}>
      {r.children ? renderRoutes(r.children) : null}
    </Route>
  ))
}

/** The default landing page depends on the user's role. */
function RootRedirect() {
  const { isClinical, isLoading } = useCurrentUser()
  if (isLoading) return null
  return <Navigate to={isClinical ? '/documents' : '/checklist'} replace />
}

function ProtectedApp({ user, onLogout }) {
  return (
    <div className="min-h-screen flex flex-col bg-plum-50">
      <TopNav user={user} onLogout={onLogout} />
      <main className="flex-1 overflow-auto">
        <div className="max-w-[1440px] mx-auto p-6">
          <Routes>
            <Route path="/" element={<RootRedirect />} />
            {renderRoutes(ROUTES)}
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState(() => {
    const saved = localStorage.getItem('user')
    const token = localStorage.getItem('session_token')
    if (saved && token) return JSON.parse(saved)
    return null
  })

  function handleLogin(data) {
    setUser({ email: data.email, name: data.name, picture: data.picture })
  }

  function handleLogout() {
    localStorage.removeItem('session_token')
    localStorage.removeItem('user')
    setUser(null)
  }

  return (
    <ConfirmProvider>
      <Routes>
        <Route path="/login" element={
          user ? <Navigate to="/" /> : <LoginPage onLogin={handleLogin} />
        } />
        <Route path="/auth/callback" element={<AuthCallback onLogin={handleLogin} />} />
        {/* Public patient pages — no staff auth, no app chrome */}
        <Route path="/p/surgery/:id" element={<PatientSurgery />} />
        {/* Public provider portal — signed-token, no login */}
        <Route path="/p/missing-charges/:token" element={<ProviderMissingChargesPortal />} />
        {/* Patient portal — own auth, own shell */}
        {/* Design preview — public, no auth, mock data */}
        <Route path="/portal/preview" element={<PreviewPortal />} />
        <Route path="/portal/login" element={<PortalLogin />} />
        <Route path="/portal/verify" element={<PortalVerify />} />
        <Route path="/portal/s/:sid" element={<PortalShell />}>
          <Route index element={<PortalDashboard />} />
          <Route path="payments" element={<Payments />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="consent" element={<Consent />} />
          <Route path="documents" element={<PortalDocuments />} />
          <Route path="messages" element={<Messages />} />
        </Route>
        {/* Pellet patient portal — own auth, own shell */}
        <Route path="/pellet-portal" element={<Navigate to="/pellet-portal/login" replace />} />
        <Route path="/pellet-portal/login" element={<PelletPortalLogin />} />
        <Route path="/pellet-portal/verify" element={<PelletPortalVerify />} />
        <Route path="/pellet-portal/home" element={<PelletPortalShell />}>
          <Route index element={<PelletDashboard />} />
          <Route path="mammo" element={<PelletMammo />} />
          <Route path="labs" element={<PelletLabs />} />
          <Route path="consent" element={<PelletConsent />} />
          <Route path="payments" element={<PelletPayments />} />
          <Route path="schedule" element={<PelletSchedule />} />
          <Route path="appointments" element={<PelletAppointments />} />
          <Route path="receipts" element={<PelletReceipts />} />
          <Route path="info" element={<PelletInfo />} />
        </Route>
        {/* Reputation review form — public, no staff auth */}
        <Route path="/r/:token" element={<ReviewForm />} />
        <Route path="/embed" element={<ReputationEmbed />} />
        <Route path="/*" element={
          user ? <ProtectedApp user={user} onLogout={handleLogout} /> : <Navigate to="/login" />
        } />
      </Routes>
    </ConfirmProvider>
  )
}
