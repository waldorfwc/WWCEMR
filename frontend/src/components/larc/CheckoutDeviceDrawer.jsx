import { X } from 'lucide-react'
import CheckoutReadyList from './CheckoutReadyList'


// Slide-in drawer launched from the LARC nav "Check Out a Device" button.
// Wraps the shared ready-to-check-out list so a device can be checked out
// from any LARC page without navigating away.
export default function CheckoutDeviceDrawer({ onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b px-4 py-3 flex items-center justify-between">
          <h2 className="font-semibold text-plum-700">Check Out a Device</h2>
          <button onClick={onClose}><X size={18} /></button>
        </div>
        <div className="p-4 space-y-3">
          <p className="text-xs text-gray-600">
            Pick the patient, read the device ID off the physical label, and type
            it in to confirm. Optionally note who you're handing it to.
          </p>
          <CheckoutReadyList />
        </div>
      </div>
    </div>
  )
}
