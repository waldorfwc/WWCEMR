import axios from 'axios'

const TOKEN_KEY = 'wwc.larc-portal.token'
const AID_KEY   = 'wwc.larc-portal.aid'

export const larcPortalApi = axios.create({ baseURL: '/api/larc-portal' })

larcPortalApi.interceptors.request.use((cfg) => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

larcPortalApi.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(AID_KEY)
      if (!location.pathname.startsWith('/larc-portal/login')) {
        location.assign('/larc-portal/login')
      }
    }
    return Promise.reject(err)
  },
)

export function setLarcSession({ token, assignment_id }) {
  localStorage.setItem(TOKEN_KEY, token)
  if (assignment_id != null) {
    localStorage.setItem(AID_KEY, assignment_id)
  }
}

export function clearLarcSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(AID_KEY)
}

export function getLarcSession() {
  return {
    token: localStorage.getItem(TOKEN_KEY),
    assignment_id: localStorage.getItem(AID_KEY),
  }
}
