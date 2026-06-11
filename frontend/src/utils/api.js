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

// MM/DD/YYYY everywhere — explicit two-digit options so locale defaults
// don't strip the leading zeros.
const _MMDDYYYY = { month: '2-digit', day: '2-digit', year: 'numeric' }
const _MMDDYYYY_HM = {
  month: '2-digit', day: '2-digit', year: 'numeric',
  hour: 'numeric', minute: '2-digit',
}
const _HM = { hour: 'numeric', minute: '2-digit' }
const _DASH = '—'


// Parse YYYY-MM-DD or an ISO datetime as the date that string starts with.
// Returns null on anything unparseable. We slice the date portion off
// datetimes deliberately so a UTC timestamp like '2026-06-10T03:00:00Z'
// displays as 06/10/2026, not the local-time equivalent (which can drift
// by a day in negative offsets). Matches the .slice(0, 10) workaround a
// third of fmt.date callers were already using.
function _parseDate(val) {
  if (val == null || val === '') return null
  if (val instanceof Date) {
    return isNaN(val.getTime()) ? null : val
  }
  const s = String(val).trim()
  if (!s) return null
  const head = s.slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(head)) return null
  const d = new Date(head + 'T00:00:00')
  return isNaN(d.getTime()) ? null : d
}


// Parse a full ISO datetime (with time + optional Z/offset) for dateTime
// rendering. Returns null on garbage.
//
// The backend stores naive UTC (now_utc_naive().isoformat()) and ships
// strings like '2026-06-10T19:30:00.123456' — no timezone marker. The
// browser's Date constructor parses those as LOCAL time per ES spec,
// which produced silent off-by-N-hours displays everywhere (Fable UX
// critique: "due 21:30" rendered for a 5:30 PM task). Append a Z when
// no timezone suffix is present so the resulting Date is actually UTC,
// and toLocaleTimeString/toLocaleString then convert to the user's
// local time correctly.
function _parseDateTime(val) {
  if (val == null || val === '') return null
  if (val instanceof Date) {
    return isNaN(val.getTime()) ? null : val
  }
  let s = String(val)
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s)
      && !/[Zz]|[+-]\d{2}:?\d{2}$/.test(s)) {
    s += 'Z'
  }
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}


function _money(val) {
  const n = parseFloat(val)
  return Number.isFinite(n) ? n : 0
}


export const fmt = {
  currency: (val) => `$${_money(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  date: (val) => {
    const d = _parseDate(val)
    return d ? d.toLocaleDateString('en-US', _MMDDYYYY) : _DASH
  },
  dateTime: (val) => {
    const d = _parseDateTime(val)
    return d ? d.toLocaleString('en-US', _MMDDYYYY_HM) : _DASH
  },
  // Short time-of-day for status chips ("Delivered · 3:42 PM").
  time: (val) => {
    const d = _parseDateTime(val)
    return d ? d.toLocaleTimeString('en-US', _HM) : ''
  },
  pct: (val) => `${_money(val).toFixed(1)}%`,
  faxStatus: (status) => {
    switch (status) {
      case 'queued':    return '⟳ Queued'
      case 'sent':      return '⟳ Sending'
      case 'delivered': return '✓ Delivered'
      case 'failed':    return '✗ Failed'
      default:          return status || _DASH
    }
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
