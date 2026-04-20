import { useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import TopNav from './components/layout/TopNav'
import Dashboard from './pages/Dashboard'
import Claims from './pages/Claims'
import ClaimDetail from './pages/ClaimDetail'
import Patients from './pages/Patients'
import PatientDetail from './pages/PatientDetail'
import Denials from './pages/Denials'
import Admin from './pages/Admin'
import Appeals from './pages/Appeals'
import ImportFiles from './pages/ImportFiles'
import AuditLog from './pages/AuditLog'
import ARDashboard from './pages/ARDashboard'
import Documents from './pages/Documents'
import PatientChart from './pages/PatientChart'
import { LoginPage, AuthCallback } from './pages/Login'
import { useCurrentUser } from './hooks/useCurrentUser'

function ProtectedApp({ user, onLogout }) {
  const { isAdmin, isClinical, isLoading } = useCurrentUser()

  return (
    <div className="min-h-screen flex flex-col bg-plum-50">
      <TopNav user={user} onLogout={onLogout} />
      <main className="flex-1 overflow-auto">
        <div className="max-w-[1440px] mx-auto p-6">
          <Routes>
            <Route path="/" element={
              isLoading ? null : (isClinical ? <Navigate to="/documents" replace /> : <Dashboard />)
            } />
            <Route path="/ar"                  element={<ARDashboard />} />
            <Route path="/documents"           element={<Documents />} />
            <Route path="/chart/:chartNumber"  element={<PatientChart />} />
            <Route path="/claims"              element={<Claims />} />
            <Route path="/claims/:id"          element={<ClaimDetail />} />
            <Route path="/patients"            element={<Patients />} />
            <Route path="/patients/:id"        element={<PatientDetail />} />
            <Route path="/denials"             element={<Denials />} />
            <Route path="/appeals"             element={<Appeals />} />
            <Route path="/import"              element={<ImportFiles />} />
            <Route path="/audit"               element={<AuditLog />} />
            <Route path="/admin" element={
              isLoading ? null : (isAdmin ? <Admin /> : <Navigate to="/" replace />)
            } />
            <Route path="*"                    element={<Navigate to="/" />} />
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
    <Routes>
      <Route path="/login" element={
        user ? <Navigate to="/" /> : <LoginPage onLogin={handleLogin} />
      } />
      <Route path="/auth/callback" element={<AuthCallback onLogin={handleLogin} />} />
      <Route path="/*" element={
        user ? <ProtectedApp user={user} onLogout={handleLogout} /> : <Navigate to="/login" />
      } />
    </Routes>
  )
}
