import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../utils/api'
import logoFull from '../assets/wwc-logo-full.png'

const GOOGLE_CLIENT_ID = '809279713851-25djaim2erm6an33n7acpqmg2jgsc5d0.apps.googleusercontent.com'

function getRedirectUri() {
  return `${window.location.origin}/auth/callback`
}

function getGoogleAuthUrl() {
  const params = new URLSearchParams({
    client_id: GOOGLE_CLIENT_ID,
    redirect_uri: getRedirectUri(),
    response_type: 'code',
    scope: 'openid email profile',
    access_type: 'offline',
    prompt: 'select_account',
  })
  return `https://accounts.google.com/o/oauth2/v2/auth?${params}`
}

export function LoginPage({ onLogin }) {
  return (
    <div className="min-h-screen bg-plum-50 flex items-center justify-center p-6">
      <div className="bg-white rounded-xl border border-border-subtle p-8 w-[420px] text-center">
        <img src={logoFull} alt="WWC Gynecology & Aesthetics" className="w-40 mx-auto mb-6" />
        <div className="mb-6">
          <div className="font-serif text-xl text-ink">Revenue &amp; Records Workspace</div>
          <div className="text-xs text-muted mt-1">Maryland · Internal Use Only</div>
        </div>

        <a
          href={getGoogleAuthUrl()}
          className="inline-flex items-center gap-3 px-6 py-3 bg-white border-2 border-border-subtle rounded-lg hover:border-plum-400 hover:shadow-md transition-all text-sm font-medium text-ink"
        >
          <svg width="20" height="20" viewBox="0 0 48 48">
            <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
            <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
            <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
            <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
          </svg>
          Sign in with Google
        </a>

        <div className="mt-6 text-xs text-muted">
          Access restricted to @waldorfwomenscare.com and @caribcall.com
        </div>
      </div>
    </div>
  )
}

export function AuthCallback({ onLogin }) {
  const [params] = useSearchParams()

  useEffect(() => {
    const code = params.get('code')
    if (!code) return

    api.post('/auth/google', {
      code,
      redirect_uri: getRedirectUri(),
    }).then(res => {
      localStorage.setItem('session_token', res.data.token)
      localStorage.setItem('user', JSON.stringify({
        email: res.data.email,
        name: res.data.name,
        picture: res.data.picture,
      }))
      onLogin(res.data)
      window.location.href = '/'
    }).catch(err => {
      console.error('Auth failed:', err)
      window.location.href = '/login?error=auth_failed'
    })
  }, [params])

  return (
    <div className="min-h-screen bg-plum-50 flex items-center justify-center">
      <div className="text-muted text-sm">Signing in...</div>
    </div>
  )
}
