import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'
import { Trophy } from 'lucide-react'

export default function AdminReputationLeaderboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['reputation-leaderboard'],
    queryFn: () => api.get('/admin/reputation/leaderboard').then(r => r.data),
    refetchInterval: 60_000,
  })
  if (isLoading) return <LoadingState />
  const rows = data?.rows || []
  return (
    <div className="p-4 max-w-5xl">
      <h1 className="page-title mb-4">Leaderboard</h1>
      {rows.length === 0 ? (
        <div className="text-sm text-muted">No profiles yet.</div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr className="text-left text-xs uppercase text-muted">
                <th className="px-4 py-2">#</th>
                <th className="px-4 py-2">Employee</th>
                <th className="px-4 py-2 text-center">Scans</th>
                <th className="px-4 py-2 text-center">Reviews</th>
                <th className="px-4 py-2 text-center">5-star</th>
                <th className="px-4 py-2 text-center">Google</th>
                <th className="px-4 py-2 text-right">Points</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((r, i) => (
                <tr key={r.profile_id} className={!r.active ? 'opacity-50' : ''}>
                  <td className="px-4 py-2 text-xs text-muted">
                    {i === 0 ? <Trophy size={14} className="text-amber-500" /> : i + 1}
                  </td>
                  <td className="px-4 py-2">
                    <div className="font-medium">{r.display_name}</div>
                    {r.role_label && (
                      <div className="text-xs text-muted">{r.role_label}</div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-center">{r.scan_points}</td>
                  <td className="px-4 py-2 text-center">{r.review_count}</td>
                  <td className="px-4 py-2 text-center">{r.five_star_count}</td>
                  <td className="px-4 py-2 text-center">{r.google_share_count}</td>
                  <td className="px-4 py-2 text-right font-semibold">{r.points}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
