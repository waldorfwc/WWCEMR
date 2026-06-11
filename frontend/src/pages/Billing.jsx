import { NavLink, Outlet } from 'react-router-dom'
import { Banknote, Receipt, FileScan, Stethoscope, Phone } from 'lucide-react'


const TABS = [
  { to: '/billing/bank-recon',          label: 'Bank Recon',          icon: Banknote },
  { to: '/billing/missing-charges',     label: 'Missing Charges',     icon: Receipt },
  { to: '/billing/insurance-documents', label: 'Insurance Documents', icon: FileScan },
  { to: '/billing/insurance-contacts',  label: 'Insurance Contacts',  icon: Phone },
  { to: '/billing/code-helper',         label: 'Code Helper',         icon: Stethoscope },
]


export default function Billing() {
  return (
    <div>
      <div className="mb-4">
        <h1 className="page-title">Billing</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Bank reconciliation, missing-charge tracking, and insurance-document workflow.
        </p>
      </div>

      <div className="border-b border-border-subtle mb-4 -mx-6 px-6">
        <nav className="flex gap-1">
          {TABS.map(t => {
            const Icon = t.icon
            return (
              <NavLink key={t.to} to={t.to}
                       className={({ isActive }) =>
                         `flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition ${
                           isActive
                             ? 'border-plum-600 text-plum-700'
                             : 'border-transparent text-gray-500 hover:text-plum-700 hover:border-plum-200'
                         }`
                       }>
                <Icon size={14} />
                {t.label}
              </NavLink>
            )
          })}
        </nav>
      </div>

      <Outlet />
    </div>
  )
}
