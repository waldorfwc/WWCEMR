import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'
import { Star } from 'lucide-react'
import EmptyState from '../components/EmptyState'
import LoadingState from '../components/LoadingState'

export default function AdminReputationReviews() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['reputation-reviews'],
    queryFn: () => api.get('/admin/reputation/reviews').then(r => r.data),
  })
  const approve = useMutation({
    mutationFn: ({ id, approved_for_embed }) =>
      api.patch(`/admin/reputation/reviews/${id}`, { approved_for_embed }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reputation-reviews'] }),
  })
  if (isLoading) return <LoadingState />
  const reviews = data?.reviews || []
  return (
    <div className="p-4 max-w-5xl">
      <h1 className="text-2xl font-semibold mb-4">Reviews</h1>
      {reviews.length === 0 ? (
        <EmptyState
          icon={Star}
          title="No reviews yet"
          body="Patient reviews submitted through the portal will appear here for moderation."
        />
      ) : (
        <div className="space-y-3">
          {reviews.map(r => (
            <div key={r.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="flex gap-0.5">
                      {[1,2,3,4,5].map(n => (
                        <Star key={n} size={14}
                              className={n <= r.stars
                                            ? 'fill-yellow-400 stroke-yellow-500'
                                            : 'stroke-gray-300'}
                              strokeWidth={1.5} />
                      ))}
                    </div>
                    <span className="text-xs text-muted">
                      {r.submitted_at?.slice(0, 16).replace('T', ' ')}
                    </span>
                  </div>
                  <div className="text-xs text-muted">
                    For <strong className="text-gray-900">{r.profile_display_name}</strong>
                  </div>
                  {r.body && (
                    <p className="text-sm mt-2 whitespace-pre-wrap">{r.body}</p>
                  )}
                  <div className="text-xs text-muted mt-2 flex flex-wrap gap-x-3 gap-y-1">
                    {r.patient_first_name && (
                      <span>From: {r.patient_first_name} {r.patient_last_initial || ''}</span>
                    )}
                    {r.patient_chart_number && (
                      <span className="text-amber-700">
                        🔒 Chart #{r.patient_chart_number}
                      </span>
                    )}
                    {r.patient_phone && (
                      <span className="text-amber-700">📞 {r.patient_phone}</span>
                    )}
                    {r.google_clicked_at && (
                      <span className="text-green-700">→ Google share clicked</span>
                    )}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  {r.consent_to_display ? (
                    <label className="flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer">
                      <input type="checkbox" checked={r.approved_for_embed}
                              onChange={e => approve.mutate({
                                id: r.id,
                                approved_for_embed: e.target.checked,
                              })}
                              disabled={approve.isPending} />
                      Show on website
                    </label>
                  ) : (
                    <span className="text-xs text-muted">No display consent</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
