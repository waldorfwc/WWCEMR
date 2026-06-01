import { getPortalViewer } from '../../lib/portal-api'

export default function PreviewBanner() {
  const viewer = getPortalViewer()
  if (!viewer?.startsWith('staff:')) return null
  const email = viewer.slice('staff:'.length)
  return (
    <div className="bg-amber-100 border-b border-amber-300 px-4 py-2
                       text-center text-sm text-amber-900">
      <strong>Preview mode</strong> — viewing as patient (read-only).
      Signed in as <strong>{email}</strong>.
    </div>
  )
}
