/**
 * Public, no-auth preview of the Start LARC Process flow — mirrors the
 * /portal/preview pattern. Renders the real StartLarcProcessDrawer with mock
 * data + stub handlers so the intake → advisory suggestion flow can be viewed
 * (and screenshotted headlessly) without authentication or touching prod data.
 *
 * Route: /larc/preview
 */
import { useState } from 'react'
import { StartLarcProcessDrawer } from './Larc'

const TYPES = [
  { id: 'dt-mirena',   name: 'Mirena (in stock)',        is_active: true },
  { id: 'dt-kyleena',  name: 'Kyleena (pharmacy order)', is_active: true },
  { id: 'dt-novasure', name: 'NovaSure (office procedure)', is_active: true },
]

const CLINICIANS = [
  { email: 'acooke@waldorfwomenscare.com', display_name: 'Aryian Cooke', npi: '1234567890', credential: 'MD', clinician_role: 'provider' },
  { email: 'app@waldorfwomenscare.com',    display_name: 'Jordan Lee',   npi: '9876543210', credential: 'NP', clinician_role: 'app' },
]

const CONFIG = {
  reason_for_request_options: [
    { reason: 'Contraception', icd10: 'Z30.430' },
    { reason: 'Menorrhagia',   icd10: 'N92.0' },
  ],
}

// Mirror the backend suggest_flow rules so the three paths demo correctly.
const SUGGESTIONS = {
  'dt-mirena':   { suggested_flow: 'in_stock',        in_stock_count: 3, default_flow: 'pharmacy_order',   allowed_flows: ['in_stock', 'pharmacy_order'] },
  'dt-kyleena':  { suggested_flow: 'pharmacy_order',  in_stock_count: 0, default_flow: 'pharmacy_order',   allowed_flows: ['pharmacy_order'] },
  'dt-novasure': { suggested_flow: 'office_procedure', in_stock_count: 0, default_flow: 'office_procedure', allowed_flows: ['office_procedure'] },
}

export default function LarcStartPreview() {
  const [done, setDone] = useState(null)

  const mock = {
    types: TYPES,
    clinicians: CLINICIANS,
    config: CONFIG,
    suggest: (deviceTypeId) => SUGGESTIONS[deviceTypeId] || SUGGESTIONS['dt-kyleena'],
    create: (payload) => { setDone(payload); return { id: 'preview' } },
  }

  return (
    <div className="min-h-screen bg-plum-50">
      <div className="max-w-[1440px] mx-auto p-6">
        <div className="rounded border border-amber-300 bg-amber-50 text-amber-800 px-4 py-2 text-sm mb-4">
          Preview — Start LARC Process (mock data, no auth, nothing is saved).
        </div>
        {done && (
          <pre className="rounded border bg-white p-3 text-xs overflow-auto">
            {JSON.stringify(done, null, 2)}
          </pre>
        )}
        <StartLarcProcessDrawer
          mock={mock}
          onClose={() => {}}
          onCreated={() => {}}
        />
      </div>
    </div>
  )
}
