/**
 * Public, no-auth preview of the To Bill worklist — renders the real
 * LarcToBill page with mock rows so the inserted (claim-entry) and
 * awaiting-insertion states can be screenshotted without staff auth.
 * TEMPORARY — remove after visual verification.
 *
 * Route: /larc/to-bill-preview
 */
import LarcToBill from './LarcToBill'

const MOCK = {
  items: [
    {
      assignment_id: 'a-1', patient_name: 'Doe, Jane', chart_number: 'MRN1001',
      device_our_id: 'WWC-0700', device_type_name: 'Mirena',
      device_ownership: 'wwc_owned', checked_out_at: '2026-06-02T09:00:00',
      inserted: true, claim_number: null,
    },
    {
      assignment_id: 'a-2', patient_name: 'Roe, Mary', chart_number: 'MRN1002',
      device_our_id: 'WWC-0711', device_type_name: 'Liletta',
      device_ownership: 'wwc_claimed', checked_out_at: '2026-06-04T13:30:00',
      inserted: false, claim_number: null,
    },
  ],
}

export default function LarcToBillPreview() {
  return (
    <div className="min-h-screen bg-plum-50">
      <div className="max-w-[1440px] mx-auto p-6">
        <div className="rounded border border-amber-300 bg-amber-50 text-amber-800 px-4 py-2 text-sm mb-4">
          Preview — To Bill (mock data, no auth, nothing is saved).
        </div>
        <LarcToBill mock={MOCK} />
      </div>
    </div>
  )
}
