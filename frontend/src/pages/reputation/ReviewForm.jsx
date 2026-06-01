import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Star } from 'lucide-react'
import axios from 'axios'

const api = axios.create({ baseURL: '/api/r' })

// TODO swap with real Google Business URL during smoke test (T10)
const GOOGLE_REVIEW_URL = "https://search.google.com/local/writereview?placeid=YOUR_PLACE_ID"

export default function ReviewForm() {
  const { token } = useParams()
  const [profile, setProfile] = useState(null)
  const [scanError, setScanError] = useState(null)
  const [stars, setStars] = useState(0)
  const [body, setBody] = useState('')
  const [showVerify, setShowVerify] = useState(false)
  const [verifyState, setVerifyState] = useState('idle')   // idle | sending | code | verified | error
  const [phone, setPhone] = useState('')
  const [challengeToken, setChallengeToken] = useState(null)
  const [code, setCode] = useState('')
  const [matchedChart, setMatchedChart] = useState(null)
  const [verifyError, setVerifyError] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastInitial, setLastInitial] = useState('')
  const [consent, setConsent] = useState(false)
  const [submitState, setSubmitState] = useState('idle')   // idle | submitting | done | error
  const [submitError, setSubmitError] = useState('')
  const [reviewId, setReviewId] = useState(null)
  const [offerGoogle, setOfferGoogle] = useState(false)

  // Scan on mount — fires exactly once per token
  useEffect(() => {
    if (!token) return
    api.post(`/${token}/scan`).then(r => {
      setProfile(r.data)
    }).catch(e => {
      setScanError(e?.response?.data?.detail || 'This QR code is no longer active.')
    })
  }, [token])

  if (scanError) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-gray-50">
        <div className="bg-white rounded-lg shadow p-6 max-w-md text-center">
          <h1 className="text-lg font-semibold text-gray-800">Hmm — this code isn't working.</h1>
          <p className="text-sm text-gray-600 mt-2">{scanError}</p>
          <p className="text-xs text-gray-500 mt-4">
            If you'd still like to share your experience, please call our office at 240-252-2140.
          </p>
        </div>
      </div>
    )
  }

  if (!profile) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }

  if (submitState === 'done') {
    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-gray-50">
        <div className="bg-white rounded-lg shadow p-6 max-w-md text-center space-y-4">
          <h1 className="text-xl font-semibold text-plum-700">Thank you!</h1>
          <p className="text-sm text-gray-700">
            Your feedback for {profile.display_name} has been received.
          </p>
          {offerGoogle && (
            <div className="pt-2">
              <p className="text-sm text-gray-700 mb-3">
                Would you help others find us by sharing this on Google?
              </p>
              <button
                onClick={async () => {
                  try {
                    await api.post(`/${token}/google-clicked`, { review_id: reviewId })
                  } catch {}
                  window.location.assign(GOOGLE_REVIEW_URL)
                }}
                className="btn-primary text-sm"
              >
                Share on Google →
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }

  async function sendCode() {
    setVerifyState('sending')
    setVerifyError('')
    try {
      const { data } = await api.post(`/${token}/verify-patient/start`, { phone })
      setChallengeToken(data.challenge_token)
      setVerifyState('code')
    } catch (e) {
      setVerifyError(e?.response?.data?.detail || 'Could not send code')
      setVerifyState('error')
    }
  }

  async function checkCode() {
    setVerifyState('sending')
    setVerifyError('')
    try {
      const { data } = await api.post(`/${token}/verify-patient/check`, {
        challenge_token: challengeToken,
        code,
      })
      setMatchedChart(data.chart_number)
      setVerifyState('verified')
    } catch (e) {
      setVerifyError(e?.response?.data?.detail || 'Invalid code')
      setVerifyState('error')
    }
  }

  async function submit() {
    if (stars === 0) return
    if (consent && !firstName.trim()) {
      setSubmitError('Please enter your first name to display your review.')
      return
    }
    setSubmitState('submitting')
    setSubmitError('')
    try {
      const { data } = await api.post(`/${token}/submit`, {
        stars,
        body: body.trim() || undefined,
        patient_first_name: consent ? firstName.trim() : undefined,
        patient_last_initial: consent ? lastInitial.trim() : undefined,
        patient_chart_number: matchedChart || undefined,
        patient_phone: matchedChart ? phone : undefined,
        consent_to_display: consent,
      })
      setReviewId(data.review_id)
      setOfferGoogle(data.offer_google_handoff)
      setSubmitState('done')
    } catch (e) {
      setSubmitError(e?.response?.data?.detail || 'Could not submit')
      setSubmitState('error')
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-8">
      <div className="max-w-md mx-auto bg-white rounded-lg shadow p-6 space-y-5">
        <div className="text-center">
          <div className="text-xs uppercase tracking-wider text-plum-700">WWC GYNECOLOGY</div>
          <h1 className="text-xl font-semibold text-gray-900 mt-1">
            How was your visit with {profile.display_name}?
          </h1>
          {profile.role_label && (
            <div className="text-sm text-gray-500 mt-1">{profile.role_label}</div>
          )}
        </div>

        {/* Stars */}
        <div className="flex justify-center gap-1">
          {[1, 2, 3, 4, 5].map(n => (
            <button key={n} onClick={() => setStars(n)} className="p-1">
              <Star
                size={36}
                className={n <= stars ? 'fill-yellow-400 stroke-yellow-500' : 'stroke-gray-300'}
                strokeWidth={1.5}
              />
            </button>
          ))}
        </div>

        {/* Comment */}
        <div>
          <label className="text-xs uppercase text-gray-500 block mb-1">
            Tell us more (optional)
          </label>
          <textarea
            value={body}
            onChange={e => setBody(e.target.value)}
            rows={4}
            placeholder="What stood out about your visit?"
            className="input w-full text-sm"
          />
        </div>

        {/* Optional: I'm a patient */}
        <div className="border-t border-gray-100 pt-4">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={showVerify}
              onChange={e => setShowVerify(e.target.checked)}
            />
            I'm a WWC patient (optional — links this to your visit)
          </label>

          {showVerify && verifyState !== 'verified' && (
            <div className="mt-3 space-y-2">
              {verifyState === 'idle' || verifyState === 'error' ? (
                <>
                  <input
                    type="tel"
                    placeholder="Phone number"
                    value={phone}
                    onChange={e => setPhone(e.target.value)}
                    className="input w-full text-sm"
                  />
                  <button
                    onClick={sendCode}
                    disabled={!phone.trim() || verifyState === 'sending'}
                    className="btn-secondary text-sm w-full"
                  >
                    Send code
                  </button>
                </>
              ) : verifyState === 'sending' ? (
                <div className="text-sm text-gray-500">Sending…</div>
              ) : (
                <>
                  <input
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="6-digit code"
                    value={code}
                    onChange={e => setCode(e.target.value)}
                    className="input w-full text-sm text-center text-lg tracking-widest"
                  />
                  <button
                    onClick={checkCode}
                    disabled={code.length !== 6}
                    className="btn-primary text-sm w-full"
                  >
                    Verify
                  </button>
                </>
              )}
              {verifyError && (
                <div className="text-xs text-red-600">{verifyError}</div>
              )}
            </div>
          )}

          {verifyState === 'verified' && (
            <div className="mt-3 text-sm text-green-700">
              ✓ Verified{matchedChart ? ` — matched chart #${matchedChart}` : ''}
            </div>
          )}
        </div>

        {/* Optional: display name on Webflow embed */}
        <div className="border-t border-gray-100 pt-4">
          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={consent}
              onChange={e => setConsent(e.target.checked)}
              className="mt-1"
            />
            <span>
              Display my first name and last initial publicly on the WWC website with this review.
            </span>
          </label>
          {consent && (
            <div className="mt-2 flex gap-2">
              <input
                value={firstName}
                onChange={e => setFirstName(e.target.value)}
                placeholder="First name"
                className="input flex-1 text-sm"
              />
              <input
                value={lastInitial}
                onChange={e => setLastInitial(e.target.value.slice(0, 1))}
                maxLength={1}
                placeholder="L"
                className="input w-12 text-sm text-center uppercase"
              />
            </div>
          )}
        </div>

        {submitError && (
          <div className="text-sm text-red-600">{submitError}</div>
        )}

        <button
          onClick={submit}
          disabled={stars === 0 || submitState === 'submitting'}
          className="btn-primary w-full text-base py-3"
        >
          {submitState === 'submitting' ? 'Submitting…' : 'Submit review'}
        </button>

        <div className="text-[10px] text-gray-400 text-center">
          Powered by Waldorf Women's Care · 240-252-2140
        </div>
      </div>
    </div>
  )
}
