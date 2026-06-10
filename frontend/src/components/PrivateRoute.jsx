import { Navigate } from 'react-router-dom'
import { useCurrentUser } from '../hooks/useCurrentUser'


/**
 * Route guard. Renders `children` when the current user satisfies the
 * tier requirement; otherwise redirects to `fallback`.
 *
 * Props:
 *   module     — backend module slug (e.g. 'surgery', 'pellets',
 *                'billing_bank_recon'). Pair with `tier`.
 *   tier       — numeric minimum tier (use the TIER constants from
 *                ../routes.jsx — VIEW=10, WORK=20, MANAGE=30, ADMIN=40).
 *   superAdmin — true → requires is_super_admin; module/tier ignored.
 *   fallback   — path to redirect to if the check fails (default: '/').
 *
 * No gate props (`module`/`tier`/`superAdmin` all absent) means "any
 * authenticated user" — the outer login guard in App.jsx is sufficient.
 *
 * Loading state shows nothing (avoids flash-of-content); once the user
 * info arrives, either renders or redirects.
 *
 * Examples:
 *   <PrivateRoute module="surgery" tier={TIER.WORK}><SurgeryWaitlist/></PrivateRoute>
 *   <PrivateRoute superAdmin><AdminPermissions/></PrivateRoute>
 */
export default function PrivateRoute({
  module, tier, superAdmin, fallback = '/', children,
}) {
  const { moduleTiers, isSuperAdmin, isLoading } = useCurrentUser()
  if (isLoading) return null

  if (superAdmin) {
    return isSuperAdmin ? children : <Navigate to={fallback} replace />
  }
  if (module && tier != null) {
    // Super-admin always passes tier checks too — matches backend
    // requires_tier behaviour.
    if (isSuperAdmin) return children
    const actual = moduleTiers?.[module] ?? 0
    if (actual < tier) return <Navigate to={fallback} replace />
  }
  return children
}
