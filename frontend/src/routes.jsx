/**
 * Staff route table. One declarative entry per protected route — each row
 * knows what module + tier (or super-admin flag) gates it. App.jsx maps
 * this table through renderRoutes() so every route gets the same guarantees:
 *   - tier check via <PrivateRoute>
 *   - URL is the source of truth; nav components read the same table
 *
 * Conventions:
 *   - module: backend Module slug from app/permissions/catalog.py (the
 *     string `Module.X.value`, not the enum name). Examples: 'active_ar',
 *     'billing_bank_recon', 'device_larc', 'pellets', 'my_checklist'.
 *   - tier: minimum tier required. Use the TIER constants below — they
 *     match backend Tier ordinals exactly.
 *   - superAdmin: true → bypasses the module/tier check; user must be
 *     Super Admin. Use for admin-console routes.
 *   - No gate fields → any authenticated staff user can reach it. The
 *     route still requires login (the parent guard in App.jsx ensures it).
 *   - children: for layouts that wrap nested routes (Billing). The parent
 *     entry can omit module/tier — each child carries its own gate.
 *   - nav: { label, order, module?, tier? } — when present, this route
 *     appears in TopNav. Visibility re-uses module/tier from the route;
 *     pass nav.module / nav.tier to override (used by layouts whose route
 *     gate differs from what should drive nav visibility).
 */
import { Navigate } from 'react-router-dom'

// Pages — staff
import ARDashboard from './pages/ARDashboard'
import ActiveAR from './pages/ActiveAR'
import ActiveARDetail from './pages/ActiveARDetail'
import Admin from './pages/Admin'
import AdminConsentTemplates from './pages/AdminConsentTemplates'
import AdminGoogleSync from './pages/AdminGoogleSync'
import AdminReputationLeaderboard from './pages/AdminReputationLeaderboard'
import AdminReputationProfiles from './pages/AdminReputationProfiles'
import AdminReputationReviews from './pages/AdminReputationReviews'
import AdminTemplates from './pages/AdminTemplates'
import AdminTraining from './pages/AdminTraining'
import AdminTrainingCards from './pages/AdminTrainingCards'
import AdminLarcPharmacies from './pages/admin/LarcPharmacies'
import AdminPermissions from './pages/admin/AdminPermissions'
import Appeals from './pages/Appeals'
import AuditLog from './pages/AuditLog'
import BankRecon from './pages/BankRecon'
import Billing from './pages/Billing'
import ChecklistNav from './components/checklist/ChecklistNav'
import ChecklistSettings from './pages/ChecklistSettings'
import ClaimDetail from './pages/ClaimDetail'
import Claims from './pages/Claims'
import CodeHelper from './pages/CodeHelper'
import CodeHelperDenials from './pages/CodeHelperDenials'
import Denials from './pages/Denials'
import Documents from './pages/Documents'
import ImportFiles from './pages/ImportFiles'
import InsuranceContacts from './pages/InsuranceContacts'
import InsuranceDocuments from './pages/InsuranceDocuments'
import Larc from './pages/Larc'
import LarcAssignment from './pages/LarcAssignment'
import LarcAudit from './pages/LarcAudit'
import LarcCheckouts from './pages/LarcCheckouts'
import LarcDevice from './pages/LarcDevice'
import LarcDevices from './pages/LarcDevices'
import LarcDeviceTypes from './pages/LarcDeviceTypes'
import LarcEodReport from './pages/LarcEodReport'
import LarcInventoryCount from './pages/LarcInventoryCount'
import LarcManual from './pages/LarcManual'
import LarcOwed from './pages/LarcOwed'
import LarcPharmacies from './pages/LarcPharmacies'
import LarcSettings from './pages/LarcSettings'
import LarcNav from './components/larc/LarcNav'
import ManagerDashboard from './pages/ManagerDashboard'
import MissingCharges from './pages/MissingCharges'
import MyChecklist from './pages/MyChecklist'
import MyProfile from './pages/MyProfile'
import PatientChart from './pages/PatientChart'
import PatientDetail from './pages/PatientDetail'
import Patients from './pages/Patients'
import PelletNav from './components/pellet/PelletNav'
import PelletAudit from './pages/PelletAudit'
import PelletCountDetail from './pages/PelletCountDetail'
import PelletCounts from './pages/PelletCounts'
import PelletDoseTypes from './pages/PelletDoseTypes'
import PelletManual from './pages/PelletManual'
import PelletPatientDetail from './pages/PelletPatientDetail'
import PelletPatients from './pages/PelletPatients'
import PelletSettings from './pages/PelletSettings'
import Pellets from './pages/Pellets'
import PracticeSettings from './pages/admin/PracticeSettings'
import RecallNav from './components/recall/RecallNav'
import Recalls from './pages/Recalls'
import RecallSettings from './pages/RecallSettings'
import StaffInbox from './pages/StaffInbox'
import StaffMessageTemplates from './pages/StaffMessageTemplates'
import Surgery from './pages/Surgery'
import SurgeryBlockSchedule from './pages/SurgeryBlockSchedule'
import SurgeryBulkImport from './pages/SurgeryBulkImport'
import SurgeryCalendar from './pages/SurgeryCalendar'
import SurgeryDetail from './pages/SurgeryDetail'
import SurgeryFeeSchedule from './pages/SurgeryFeeSchedule'
import SurgerySettings from './pages/SurgerySettings'
import SurgeryWaitlist from './pages/SurgeryWaitlist'
import SurgeryNav from './components/surgery/SurgeryNav'
import MarketingNav from './components/marketing/MarketingNav'

// Mirrors backend Tier ordinals (app/permissions/catalog.py).
export const TIER = {
  VIEW: 10,
  WORK: 20,
  MANAGE: 30,
  ADMIN: 40,
}

// Module slug constants. Mirrors backend Module enum values
// (app/permissions/catalog.py). Use these instead of hand-typing slugs
// so renaming a module surfaces every callsite and IDEs can autocomplete.
export const MODULE = {
  CHART:              'chart',
  ACTIVE_AR:          'active_ar',
  BANK_RECON:         'billing_bank_recon',
  MISSING_CHARGES:    'billing_missing_charges',
  INSURANCE_DOCS:     'billing_insurance_docs',
  INSURANCE_CONTACTS: 'billing_insurance_contacts',
  RECALL:             'recall',
  REPUTATION:         'reputation',
  SURGERY:            'surgery',
  LARC:               'device_larc',
  PELLETS:            'pellets',
  TRAINING:           'training',
  MY_CHECKLIST:       'my_checklist',
  AUDIT_LOG:          'audit_log',
}
const M = MODULE

export const ROUTES = [
  // ── Personal — every authenticated staff user ───────────────────
  { path: '/me',        element: <MyProfile /> },
  // Layout route: ChecklistNav renders the shared top-nav + <Outlet/> for the
  // child page. No module/tier — matches the previously ungated /checklist
  // (any authed user). Keep nav on the parent so the sidebar "My Checklist"
  // entry persists. Child paths are RELATIVE.
  { path: '/checklist', element: <ChecklistNav />,
      nav: { label: 'My Checklist', order: 10 },
      children: [
    { index: true,      element: <MyChecklist /> },
    { path: 'settings', element: <ChecklistSettings /> },
  ]},

  // ── Active AR + claims ─────────────────────────────────────────
  { path: '/ar',             element: <ARDashboard />,    module: M.ACTIVE_AR, tier: TIER.VIEW },
  { path: '/active-ar',      element: <ActiveAR />,       module: M.ACTIVE_AR, tier: TIER.VIEW,
      nav: { label: 'Active AR', order: 30 } },
  { path: '/active-ar/:id',  element: <ActiveARDetail />, module: M.ACTIVE_AR, tier: TIER.VIEW },
  { path: '/claims',         element: <Claims />,         module: M.ACTIVE_AR, tier: TIER.VIEW },
  { path: '/claims/:id',     element: <ClaimDetail />,    module: M.ACTIVE_AR, tier: TIER.VIEW },
  { path: '/denials',        element: <Denials />,        module: M.ACTIVE_AR, tier: TIER.VIEW },
  { path: '/appeals',        element: <Appeals />,        module: M.ACTIVE_AR, tier: TIER.VIEW },
  { path: '/import',         element: <ImportFiles />,    module: M.ACTIVE_AR, tier: TIER.MANAGE },

  // ── Billing layout + nested billing tools ──────────────────────
  // The /billing layout itself has no gate — each child carries one.
  // The nav entry uses active_ar:VIEW as a coarse shorthand for "billing
  // staff", matching the existing isBilling role flag. Anyone with
  // bank-recon-only access can still navigate via deep link.
  // Legacy /bank-recon URL → redirect to nested route.
  { path: '/bank-recon', element: <Navigate to="/billing/bank-recon" replace /> },
  { path: '/billing', element: <Billing />,
      nav: { label: 'Billing', order: 40, module: M.ACTIVE_AR, tier: TIER.VIEW },
      children: [
    { path: '',                  element: <Navigate to="bank-recon" replace /> },
    { path: 'bank-recon',         element: <BankRecon />,           module: M.BANK_RECON,         tier: TIER.VIEW },
    { path: 'missing-charges',    element: <MissingCharges />,      module: M.MISSING_CHARGES,    tier: TIER.VIEW },
    { path: 'insurance-documents', element: <InsuranceDocuments />, module: M.INSURANCE_DOCS,     tier: TIER.VIEW },
    { path: 'insurance-contacts',  element: <InsuranceContacts />,  module: M.INSURANCE_CONTACTS, tier: TIER.VIEW },
    // Code Helper is a billing/claims utility — gate on Active AR.
    { path: 'code-helper',         element: <CodeHelper />,         module: M.ACTIVE_AR,          tier: TIER.VIEW },
    { path: 'code-helper/denials', element: <CodeHelperDenials />,  module: M.ACTIVE_AR,          tier: TIER.VIEW },
  ]},

  // ── Manager dashboard — checklist owners ───────────────────────
  { path: '/manager-dashboard', element: <ManagerDashboard />, module: M.MY_CHECKLIST, tier: TIER.MANAGE },

  // ── Recalls ────────────────────────────────────────────────────
  // Layout route: RecallNav renders the shared top-nav + <Outlet/> for the
  // child page. Each child carries its own gate. Keep nav on the parent so
  // the TopNav "Recalls" entry persists. Child paths are RELATIVE.
  { path: '/recalls', element: <RecallNav />, module: M.RECALL, tier: TIER.WORK,
      nav: { label: 'Recalls', order: 50 },
      children: [
    { index: true,      element: <Recalls /> },
    { path: 'settings', element: <RecallSettings />, module: M.RECALL, tier: TIER.MANAGE },
  ]},

  // ── Surgery ────────────────────────────────────────────────────
  // Layout route: SurgeryNav renders the shared top-nav + <Outlet/> for the
  // child page. Each child carries its own gate. Keep nav on the parent so
  // the TopNav "Surgery" entry persists. Child paths are RELATIVE.
  { path: '/surgery', element: <SurgeryNav />, module: M.SURGERY, tier: TIER.VIEW,
      nav: { label: 'Surgery', order: 60 },
      children: [
    { index: true,            element: <Surgery />,             module: M.SURGERY, tier: TIER.VIEW },
    { path: 'settings',       element: <SurgerySettings />,     module: M.SURGERY, tier: TIER.MANAGE },
    { path: 'rules',          element: <Navigate to="/surgery/settings" replace />, module: M.SURGERY, tier: TIER.MANAGE },
    { path: 'block-schedule', element: <SurgeryBlockSchedule />, module: M.SURGERY, tier: TIER.MANAGE },
    { path: 'waitlist',       element: <SurgeryWaitlist />,     module: M.SURGERY, tier: TIER.WORK },
    { path: 'calendar',       element: <SurgeryCalendar />,     module: M.SURGERY, tier: TIER.VIEW },
    { path: 'bulk-import',    element: <SurgeryBulkImport />,   module: M.SURGERY, tier: TIER.MANAGE },
    { path: 'fee-schedule',   element: <SurgeryFeeSchedule />,  module: M.SURGERY, tier: TIER.MANAGE },
    { path: 'messages',       element: <StaffInbox />,          module: M.SURGERY, tier: TIER.WORK },
    { path: ':id',            element: <SurgeryDetail />,       module: M.SURGERY, tier: TIER.VIEW },
  ]},

  // ── LARC device tracking ───────────────────────────────────────
  // Layout route: LarcNav renders the shared top-nav + <Outlet/> for the
  // child page. Each child carries its own gate. Keep nav on the parent so
  // the TopNav "Device Tracking" entry persists. Child paths are RELATIVE.
  { path: '/larc', element: <LarcNav />, module: M.LARC, tier: TIER.VIEW,
      nav: { label: 'Device Tracking', order: 70 },
      children: [
    { index: true,             element: <Larc />,               module: M.LARC, tier: TIER.VIEW },
    { path: 'devices',         element: <LarcDevices />,         module: M.LARC, tier: TIER.VIEW },
    { path: 'devices/:id',     element: <LarcDevice />,          module: M.LARC, tier: TIER.VIEW },
    { path: 'checkouts',       element: <LarcCheckouts />,       module: M.LARC, tier: TIER.VIEW },
    { path: 'owed',            element: <LarcOwed />,            module: M.LARC, tier: TIER.VIEW },
    { path: 'audit',           element: <LarcAudit />,           module: M.LARC, tier: TIER.MANAGE },
    { path: 'pharmacies',      element: <LarcPharmacies />,      module: M.LARC, tier: TIER.MANAGE },
    { path: 'device-types',    element: <LarcDeviceTypes />,     module: M.LARC, tier: TIER.MANAGE },
    { path: 'eod',             element: <LarcEodReport />,       module: M.LARC, tier: TIER.VIEW },
    { path: 'inventory-count', element: <LarcInventoryCount />,  module: M.LARC, tier: TIER.WORK },
    { path: 'manual',          element: <LarcManual />,          module: M.LARC, tier: TIER.VIEW },
    { path: 'assignments/:id', element: <LarcAssignment />,      module: M.LARC, tier: TIER.WORK },
    { path: 'settings',        element: <LarcSettings />,        module: M.LARC, tier: TIER.MANAGE },
  ]},

  // ── Pellets (DEA Schedule III) ─────────────────────────────────
  // Layout route: PelletNav renders the shared top-nav + <Outlet/> for the
  // child page. Each child carries its own gate. Keep nav on the parent so
  // the TopNav "Pellets" entry persists. Child paths are RELATIVE.
  { path: '/pellets', element: <PelletNav />, module: M.PELLETS, tier: TIER.VIEW,
      nav: { label: 'Pellets', order: 80 },
      children: [
    { index: true,          element: <PelletPatients />,      module: M.PELLETS, tier: TIER.VIEW },
    { path: 'inventory',    element: <Pellets />,             module: M.PELLETS, tier: TIER.VIEW },
    { path: 'counts',       element: <PelletCounts />,        module: M.PELLETS, tier: TIER.WORK },
    { path: 'counts/:id',   element: <PelletCountDetail />,   module: M.PELLETS, tier: TIER.WORK },
    { path: 'audit',        element: <PelletAudit />,         module: M.PELLETS, tier: TIER.VIEW },
    { path: 'manual',       element: <PelletManual />,        module: M.PELLETS, tier: TIER.VIEW },
    { path: 'patients',     element: <PelletPatients />,      module: M.PELLETS, tier: TIER.VIEW },
    { path: 'patients/:id', element: <PelletPatientDetail />, module: M.PELLETS, tier: TIER.VIEW },
    { path: 'dose-types',   element: <PelletDoseTypes />,     module: M.PELLETS, tier: TIER.MANAGE },
    { path: 'settings',     element: <PelletSettings />,      module: M.PELLETS, tier: TIER.MANAGE },
  ]},

  // ── Marketing (reputation / reviews) ───────────────────────────
  // Layout route: MarketingNav renders the shared top-nav + <Outlet/>.
  // Moved out of the Admin console; gated on the Reputation module tier
  // (VIEW to see reviews/leaderboard, MANAGE to configure profiles).
  { path: '/marketing', element: <MarketingNav />, module: M.REPUTATION, tier: TIER.VIEW,
      nav: { label: 'Marketing', order: 95 },
      children: [
    { index: true,         element: <AdminReputationReviews />,     module: M.REPUTATION, tier: TIER.VIEW },
    { path: 'leaderboard', element: <AdminReputationLeaderboard />, module: M.REPUTATION, tier: TIER.VIEW },
    { path: 'profiles',    element: <AdminReputationProfiles />,    module: M.REPUTATION, tier: TIER.MANAGE },
  ]},

  // ── Chart / documents / patients ───────────────────────────────
  { path: '/documents',         element: <Documents />,    module: M.CHART, tier: TIER.VIEW,
      nav: { label: 'Charts', order: 20 } },
  { path: '/chart/:chartNumber', element: <PatientChart />, module: M.CHART, tier: TIER.VIEW },
  { path: '/patients',          element: <Patients />,      module: M.CHART, tier: TIER.VIEW },
  { path: '/patients/:id',      element: <PatientDetail />, module: M.CHART, tier: TIER.VIEW },

  // ── Audit log ──────────────────────────────────────────────────
  { path: '/audit', element: <AuditLog />, module: M.AUDIT_LOG, tier: TIER.VIEW },

  // ── Admin console — super-admin only ───────────────────────────
  { path: '/admin',                            element: <Admin />,                         superAdmin: true },
  { path: '/admin/permissions',                element: <AdminPermissions />,              superAdmin: true },
  { path: '/admin/practice-settings',          element: <PracticeSettings />,              superAdmin: true },
  { path: '/admin/larc-pharmacies',            element: <AdminLarcPharmacies />,           superAdmin: true },
  { path: '/admin/templates',                  element: <AdminTemplates />,                superAdmin: true },
  { path: '/admin/consent-templates',          element: <AdminConsentTemplates />,         superAdmin: true },
  { path: '/admin/message-templates',          element: <StaffMessageTemplates />,         superAdmin: true },
  { path: '/admin/training',                   element: <AdminTraining />,                 superAdmin: true },
  { path: '/admin/training/cards',             element: <AdminTrainingCards />,            superAdmin: true },
  { path: '/admin/google-sync',                element: <AdminGoogleSync />,               superAdmin: true },
  // Reputation pages moved to top-level /marketing — keep old admin URLs working.
  { path: '/admin/reputation',             element: <Navigate to="/marketing" replace /> },
  { path: '/admin/reputation/reviews',     element: <Navigate to="/marketing" replace /> },
  { path: '/admin/reputation/leaderboard', element: <Navigate to="/marketing/leaderboard" replace /> },
  { path: '/admin/reputation/profiles',    element: <Navigate to="/marketing/profiles" replace /> },

  // ── Legacy admin URLs — unified permissions screen replaced them ──
  { path: '/admin/groups',                      element: <Navigate to="/admin/permissions" replace /> },
  { path: '/admin/users/:email/tiers',          element: <Navigate to="/admin/permissions" replace /> },
  { path: '/admin/groups/:groupId/tiers',       element: <Navigate to="/admin/permissions" replace /> },
]
