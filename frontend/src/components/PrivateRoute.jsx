import { Navigate } from 'react-router-dom'
import { useCurrentUser } from '../hooks/useCurrentUser'


/**
 * Route guard. Renders `children` when the current user satisfies the
 * permission requirement; otherwise redirects.
 *
 * Props:
 *   perm     — single permission string ('checklist:manage')
 *   anyOf    — array; user must have at least one of these perms
 *   allOf    — array; user must have all of these perms
 *   adminOnly — convenience: shortcut for the existing `isAdmin` flag
 *   fallback — path to redirect to if the check fails (default: '/')
 *
 * Loading state shows nothing (avoids flash-of-content); once the user
 * info arrives, either renders or redirects.
 *
 * Examples:
 *   <PrivateRoute perm="checklist:manage"><ManagerDashboard/></PrivateRoute>
 *   <PrivateRoute adminOnly><AdminPermissions/></PrivateRoute>
 *   <PrivateRoute anyOf={['pellet:manage','user:manage']}><DoseTypes/></PrivateRoute>
 */
export default function PrivateRoute({
  perm, anyOf, allOf, adminOnly, fallback = '/', children,
}) {
  const { has, isAdmin, isLoading } = useCurrentUser()
  if (isLoading) return null

  let allowed = true
  if (adminOnly && !isAdmin) allowed = false
  if (perm && !has?.(perm)) allowed = false
  if (anyOf && !anyOf.some(p => has?.(p))) allowed = false
  if (allOf && !allOf.every(p => has?.(p))) allowed = false

  return allowed ? children : <Navigate to={fallback} replace />
}
