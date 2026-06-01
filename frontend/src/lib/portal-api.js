import axios from 'axios'

const TOKEN_KEY = 'wwc.portal.token'
const SID_KEY   = 'wwc.portal.sid'

export const portalApi = axios.create({ baseURL: '/api/patient/portal' })

portalApi.interceptors.request.use((cfg) => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

portalApi.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(SID_KEY)
      if (!location.pathname.startsWith('/portal/login')) {
        location.assign('/portal/login')
      }
    }
    return Promise.reject(err)
  },
)

export function setPortalSession({ token, surgery_id }) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(SID_KEY, surgery_id)
}

export function clearPortalSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(SID_KEY)
}

export function getPortalSession() {
  return {
    token: localStorage.getItem(TOKEN_KEY),
    surgery_id: localStorage.getItem(SID_KEY),
  }
}

export function decodePortalToken(token) {
  if (!token) return null
  try {
    const [, b64] = token.split('.')
    // Base64URL → Base64 + padding
    const padded = b64.replace(/-/g, '+').replace(/_/g, '/')
      .padEnd(b64.length + (4 - b64.length % 4) % 4, '=')
    const json = atob(padded)
    return JSON.parse(json)
  } catch {
    return null
  }
}

export function getPortalViewer() {
  const payload = decodePortalToken(localStorage.getItem(TOKEN_KEY))
  return payload?.viewer || null
}

export function isStaffPreview() {
  return (getPortalViewer() || '').startsWith('staff:')
}
