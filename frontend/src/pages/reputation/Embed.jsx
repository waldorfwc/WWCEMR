import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Star } from 'lucide-react'
import axios from 'axios'
import { fmt } from '../../utils/api'

const api = axios.create()

export default function Embed() {
  const [params] = useSearchParams()
  const limit = Math.min(Math.max(parseInt(params.get('limit') || '20', 10) || 20, 1), 100)
  const theme = params.get('theme') === 'dark' ? 'dark' : 'light'
  const [reviews, setReviews] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.get(`/api/reviews/public?limit=${limit}`)
      .then(r => setReviews(r.data.reviews || []))
      .catch(() => setError('Could not load reviews.'))
  }, [limit])

  const isDark = theme === 'dark'
  const wrapClass = isDark
    ? 'min-h-screen bg-transparent text-gray-100'
    : 'min-h-screen bg-transparent text-gray-900'
  const cardClass = isDark
    ? 'bg-gray-800/50 border border-gray-700 rounded-lg p-4'
    : 'bg-white shadow-sm border border-border-subtle rounded-lg p-4'

  if (error) {
    return <div className={`${wrapClass} p-6 text-sm`}>{error}</div>
  }
  if (reviews === null) {
    return <div className={`${wrapClass} p-6 text-sm opacity-60`}>Loading reviews…</div>
  }
  if (reviews.length === 0) {
    return <div className={`${wrapClass} p-6 text-sm opacity-60`}>No reviews yet.</div>
  }

  return (
    <div className={`${wrapClass} p-4 space-y-3`}>
      {reviews.map((r, i) => (
        <article key={i} className={cardClass}>
          <div className="flex items-center gap-0.5 mb-2">
            {[1,2,3,4,5].map(n => (
              <Star key={n} size={16}
                     className={n <= r.stars
                                  ? 'fill-yellow-400 stroke-yellow-500'
                                  : (isDark ? 'stroke-gray-600' : 'stroke-gray-300')}
                     strokeWidth={1.5} />
            ))}
            <span className="ml-2 text-xs opacity-60">
              {fmt.date(r.submitted_at)}
            </span>
          </div>
          {r.body && (
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{r.body}</p>
          )}
          <div className="text-xs opacity-60 mt-2">— {r.display_name}</div>
        </article>
      ))}
    </div>
  )
}
