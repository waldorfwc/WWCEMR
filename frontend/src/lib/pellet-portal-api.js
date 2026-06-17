import axios from 'axios'

const TOKEN_KEY = 'wwc.pellet-portal.token'
const PID_KEY   = 'wwc.pellet-portal.pid'

export const pelletPortalApi = axios.create({ baseURL: '/api/pellet-portal' })

pelletPortalApi.interceptors.request.use((cfg) => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

pelletPortalApi.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(PID_KEY)
      if (!location.pathname.startsWith('/pellet-portal/login')) {
        location.assign('/pellet-portal/login')
      }
    }
    return Promise.reject(err)
  },
)

export function setPelletSession({ token, pellet_patient_id }) {
  localStorage.setItem(TOKEN_KEY, token)
  if (pellet_patient_id != null) {
    localStorage.setItem(PID_KEY, pellet_patient_id)
  }
}

export function clearPelletSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(PID_KEY)
}

export function getPelletSession() {
  return {
    token: localStorage.getItem(TOKEN_KEY),
    pellet_patient_id: localStorage.getItem(PID_KEY),
  }
}
