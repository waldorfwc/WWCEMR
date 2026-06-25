import { X } from 'lucide-react'
import ReturnDeviceList from './ReturnDeviceList'


// Slide-in drawer launched from the LARC nav "Update Device Status" button —
// the counterpart to Check Out a Device. Lists checked-out devices and lets
// staff update one's status with a category-appropriate reason.
export default function ReturnDeviceDrawer({ onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b px-4 py-3 flex items-center justify-between">
          <h2 className="font-semibold text-plum-700">Update Device Status</h2>
          <button onClick={onClose}><X size={18} /></button>
        </div>
        <div className="p-4 space-y-3">
          <p className="text-xs text-gray-600">
            Update the status of a checked-out device. Pick the patient/device,
            choose what happened, and confirm.
          </p>
          <ReturnDeviceList />
        </div>
      </div>
    </div>
  )
}
