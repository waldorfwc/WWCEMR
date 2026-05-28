import { useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import TopNav from './components/layout/TopNav'
import Claims from './pages/Claims'
import ClaimDetail from './pages/ClaimDetail'
import Patients from './pages/Patients'
import PatientDetail from './pages/PatientDetail'
import Denials from './pages/Denials'
import Admin from './pages/Admin'
import AdminGroups from './pages/AdminGroups'
import AdminTemplates from './pages/AdminTemplates'
import AdminConsentTemplates from './pages/AdminConsentTemplates'
import SurgeryRules from './pages/SurgeryRules'
import Larc from './pages/Larc'
import LarcAssignment from './pages/LarcAssignment'
import LarcCheckouts from './pages/LarcCheckouts'
import LarcAudit from './pages/LarcAudit'
import LarcDevices from './pages/LarcDevices'
import LarcDevice from './pages/LarcDevice'
import LarcOwed from './pages/LarcOwed'
import LarcPharmacies from './pages/LarcPharmacies'
import LarcDeviceTypes from './pages/LarcDeviceTypes'
import LarcEodReport from './pages/LarcEodReport'
import LarcInventoryCount from './pages/LarcInventoryCount'
import LarcManual from './pages/LarcManual'
import AdminTraining from './pages/AdminTraining'
import AdminTrainingCards from './pages/AdminTrainingCards'
import AdminGoogleSync from './pages/AdminGoogleSync'
import Appeals from './pages/Appeals'
import ImportFiles from './pages/ImportFiles'
import AuditLog from './pages/AuditLog'
import ARDashboard from './pages/ARDashboard'
import ActiveAR from './pages/ActiveAR'
import ActiveARDetail from './pages/ActiveARDetail'
import BankRecon from './pages/BankRecon'
import Billing from './pages/Billing'
import MissingCharges from './pages/MissingCharges'
import InsuranceDocuments from './pages/InsuranceDocuments'
import InsuranceContacts from './pages/InsuranceContacts'
import ProviderMissingChargesPortal from './pages/ProviderMissingChargesPortal'
import Pellets from './pages/Pellets'
import PelletCounts from './pages/PelletCounts'
import PelletCountDetail from './pages/PelletCountDetail'
import PelletAudit from './pages/PelletAudit'
import PelletManual from './pages/PelletManual'
import PelletPatients from './pages/PelletPatients'
import PelletPatientDetail from './pages/PelletPatientDetail'
import PelletDoseTypes from './pages/PelletDoseTypes'
import MyChecklist from './pages/MyChecklist'
import ManagerDashboard from './pages/ManagerDashboard'
import MyProfile from './pages/MyProfile'
import Recalls from './pages/Recalls'
import Surgery from './pages/Surgery'
import SurgeryDetail from './pages/SurgeryDetail'
import SurgeryBlockSchedule from './pages/SurgeryBlockSchedule'
import SurgeryWaitlist from './pages/SurgeryWaitlist'
import SurgeryCalendar from './pages/SurgeryCalendar'
import PatientSurgery from './pages/PatientSurgery'
import Documents from './pages/Documents'
import PatientChart from './pages/PatientChart'
import { LoginPage, AuthCallback } from './pages/Login'
import { useCurrentUser } from './hooks/useCurrentUser'
import PrivateRoute from './components/PrivateRoute'
import CodeHelper from './pages/CodeHelper'
import CodeHelperDenials from './pages/CodeHelperDenials'
import { ConfirmProvider } from './components/ui/ConfirmDialog'

function ProtectedApp({ user, onLogout }) {
  const { isAdmin, isClinical, isLoading } = useCurrentUser()

  return (
    <div className="min-h-screen flex flex-col bg-plum-50">
      <TopNav user={user} onLogout={onLogout} />
      <main className="flex-1 overflow-auto">
        <div className="max-w-[1440px] mx-auto p-6">
          <Routes>
            <Route path="/" element={
              isLoading ? null
                : (isClinical
                    ? <Navigate to="/documents" replace />
                    : <Navigate to="/checklist" replace />)
            } />
            <Route path="/ar"                  element={<ARDashboard />} />
            <Route path="/active-ar"           element={<ActiveAR />} />
            <Route path="/active-ar/:id"       element={<ActiveARDetail />} />
            {/* Legacy URL — redirect to nested route */}
            <Route path="/bank-recon"          element={<Navigate to="/billing/bank-recon" replace />} />
            <Route path="/billing"             element={<Billing />}>
              <Route index                     element={<Navigate to="bank-recon" replace />} />
              <Route path="bank-recon"         element={<BankRecon />} />
              <Route path="missing-charges"    element={<MissingCharges />} />
              <Route path="insurance-documents" element={<InsuranceDocuments />} />
              <Route path="insurance-contacts"  element={<InsuranceContacts />} />
              <Route path="code-helper"         element={<CodeHelper />} />
              <Route path="code-helper/denials" element={<CodeHelperDenials />} />
            </Route>
            <Route path="/checklist"           element={<MyChecklist />} />
            <Route path="/manager-dashboard"   element={
              <PrivateRoute perm="checklist:manage"><ManagerDashboard /></PrivateRoute>
            } />
            <Route path="/me"                  element={<MyProfile />} />
            <Route path="/recalls"             element={<Recalls />} />
            <Route path="/surgery"             element={<Surgery />} />
            <Route path="/surgery/rules"           element={<SurgeryRules />} />
            <Route path="/surgery/block-schedule" element={<SurgeryBlockSchedule />} />
            <Route path="/surgery/waitlist"        element={<SurgeryWaitlist />} />
            <Route path="/surgery/calendar"        element={<SurgeryCalendar />} />
            <Route path="/surgery/:id"         element={<SurgeryDetail />} />
            <Route path="/larc"                element={<Larc />} />
            <Route path="/larc/assignments/:id" element={<LarcAssignment />} />
            <Route path="/larc/checkouts"      element={<LarcCheckouts />} />
            <Route path="/larc/audit"          element={<LarcAudit />} />
            <Route path="/larc/devices"        element={<LarcDevices />} />
            <Route path="/larc/devices/:id"    element={<LarcDevice />} />
            <Route path="/larc/owed"           element={<LarcOwed />} />
            <Route path="/larc/pharmacies"     element={<LarcPharmacies />} />
            <Route path="/larc/device-types"   element={<LarcDeviceTypes />} />
            <Route path="/larc/eod"            element={<LarcEodReport />} />
            <Route path="/larc/inventory-count" element={<LarcInventoryCount />} />
            <Route path="/larc/manual"         element={<LarcManual />} />
            <Route path="/pellets"             element={<Navigate to="/pellets/patients" replace />} />
            <Route path="/pellets/inventory"   element={<Pellets />} />
            <Route path="/pellets/counts"      element={<PelletCounts />} />
            <Route path="/pellets/counts/:id"  element={<PelletCountDetail />} />
            <Route path="/pellets/audit"       element={<PelletAudit />} />
            <Route path="/pellets/manual"      element={<PelletManual />} />
            <Route path="/pellets/patients"    element={<PelletPatients />} />
            <Route path="/pellets/patients/:id" element={<PelletPatientDetail />} />
            <Route path="/pellets/dose-types"  element={<PelletDoseTypes />} />
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
            <Route path="/admin"                   element={<PrivateRoute adminOnly><Admin /></PrivateRoute>} />
            <Route path="/admin/groups"            element={<PrivateRoute adminOnly><AdminGroups /></PrivateRoute>} />
            <Route path="/admin/templates"         element={<PrivateRoute adminOnly><AdminTemplates /></PrivateRoute>} />
            <Route path="/admin/consent-templates" element={<PrivateRoute adminOnly><AdminConsentTemplates /></PrivateRoute>} />
            <Route path="/admin/training"          element={<PrivateRoute adminOnly><AdminTraining /></PrivateRoute>} />
            <Route path="/admin/training/cards"    element={<PrivateRoute adminOnly><AdminTrainingCards /></PrivateRoute>} />
            <Route path="/admin/google-sync"       element={<PrivateRoute adminOnly><AdminGoogleSync /></PrivateRoute>} />
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
        <Route path="/*" element={
          user ? <ProtectedApp user={user} onLogout={handleLogout} /> : <Navigate to="/login" />
        } />
      </Routes>
    </ConfirmProvider>
  )
}
