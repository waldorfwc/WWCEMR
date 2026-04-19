import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

api.interceptors.request.use(config => {
  const token = localStorage.getItem('session_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401 && !window.location.pathname.startsWith('/auth') && window.location.pathname !== '/login') {
      localStorage.removeItem('session_token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export const fmt = {
  currency: (val) => `$${parseFloat(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  date: (val) => val ? new Date(val + 'T00:00:00').toLocaleDateString('en-US') : '—',
  dateTime: (val) => val ? new Date(val).toLocaleString('en-US') : '—',
  pct: (val) => `${parseFloat(val || 0).toFixed(1)}%`,
  faxStatus: (status) => {
    switch (status) {
      case 'queued':    return '⟳ Queued'
      case 'sent':      return '⟳ Sending'
      case 'delivered': return '✓ Delivered'
      case 'failed':    return '✗ Failed'
      default:          return status || '—'
    }
  },
  faxDate: (iso) => {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  },
}

export const statusColors = {
  paid: 'badge-paid',
  denied: 'badge-denied',
  partial: 'badge-partial',
  pending: 'badge-pending',
  adjusted: 'badge-partial',
  appealing: 'badge-appealing',
  written_off: 'badge-written_off',
  open: 'badge-denied',
  overturned: 'badge-paid',
  upheld: 'badge-denied',
  resubmitted: 'badge-appealing',
  draft: 'badge-pending',
  submitted: 'badge-appealing',
  approved: 'badge-paid',
}

export default api
