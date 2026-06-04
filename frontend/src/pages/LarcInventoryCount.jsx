import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, ClipboardCheck, Plus, Check, X, AlertTriangle, Camera } from 'lucide-react'
import { Html5Qrcode } from 'html5-qrcode'
import api, { fmt } from '../utils/api'


export default function LarcInventoryCount() {
  const qc = useQueryClient()
  const [activeCount, setActiveCount] = useState(null)
  const [showStart, setShowStart] = useState(false)

  const { data: history = [] } = useQuery({
    queryKey: ['larc-inventory-counts'],
    queryFn: () => api.get('/larc/inventory-counts').then(r => r.data),
  })

  // Find any in-progress count and surface it
  useEffect(() => {
    const inProgress = history.find(c => c.status === 'in_progress')
    if (inProgress && !activeCount) setActiveCount(inProgress.id)
  }, [history])

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <div className="flex items-baseline justify-between mb-3">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <ClipboardCheck size={22} className="text-plum-700" />
          Physical inventory count
        </h1>
        {!activeCount && (
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setShowStart(true)}>
            <Plus size={13} /> Start count
          </button>
        )}
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Snapshot the cabinet and scan every device. Anything expected-but-not-scanned at the end
        gets marked <strong>lost</strong> for the loss-tracking report.
      </p>

      {activeCount ? (
        <ActiveCount countId={activeCount} qc={qc} onDone={() => setActiveCount(null)} />
      ) : (
        <HistoryTable history={history} onOpen={(id) => setActiveCount(id)} />
      )}

      {showStart && <StartCountForm onClose={() => setShowStart(false)}
                                       onStarted={(id) => { setShowStart(false); setActiveCount(id) }}
                                       qc={qc} />}
    </div>
  )
}


function StartCountForm({ onClose, onStarted, qc }) {
  const [scope, setScope] = useState('')
  const start = useMutation({
    mutationFn: () => api.post('/larc/inventory-counts/start',
                                { scope_location: scope || null }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['larc-inventory-counts'] })
      onStarted(data.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Start failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl"
           onClick={e => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-border-subtle flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Start Inventory Count</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Scope</label>
            <select className="input text-sm w-full" value={scope} onChange={e => setScope(e.target.value)}>
              <option value="">All locations</option>
              <option value="white_plains">White Plains</option>
              <option value="arlington">Arlington</option>
              <option value="brandywine">Brandywine</option>
            </select>
          </div>
          <button className="btn-primary text-sm w-full" onClick={() => start.mutate()}
                  disabled={start.isPending}>
            {start.isPending ? 'Starting…' : 'Start'}
          </button>
        </div>
      </div>
    </div>
  )
}


function ActiveCount({ countId, qc, onDone }) {
  const inputRef = useRef(null)
  const [draft, setDraft] = useState('')
  const [notes, setNotes] = useState('')
  const [cameraOpen, setCameraOpen] = useState(false)
  const [lastResult, setLastResult] = useState(null)

  const { data: c } = useQuery({
    queryKey: ['larc-inventory-count', countId],
    queryFn: () => api.get(`/larc/inventory-counts/${countId}`).then(r => r.data),
    refetchInterval: 5_000,
  })

  const scan = useMutation({
    mutationFn: (our_id) => api.post(`/larc/inventory-counts/${countId}/scan`,
                                       { our_id }).then(r => r.data),
    onSuccess: (data, variables) => {
      qc.invalidateQueries({ queryKey: ['larc-inventory-count', countId] })
      setDraft('')
      setLastResult({ ok: true, our_id: data.device_our_id, at: Date.now() })
      inputRef.current?.focus()
      try { beep(880, 80) } catch {}
    },
    onError: (e, variables) => {
      const msg = e?.response?.data?.detail || 'Scan failed'
      setLastResult({ ok: false, msg, raw: variables, at: Date.now() })
      try { beep(220, 200) } catch {}
      // Don't alert when scanning by camera — too disruptive
      if (!cameraOpen) alert(msg)
    },
  })

  const finish = useMutation({
    mutationFn: () => api.post(`/larc/inventory-counts/${countId}/finish`,
                                { notes: notes || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-inventory-counts'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      onDone()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Finish failed'),
  })

  if (!c) return <div className="text-gray-400 italic">Loading count…</div>

  return (
    <div className="space-y-3">
      <div className="card !p-3">
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
          Active count · {c.scope_location ? c.scope_location.replace('_', ' ') : 'all locations'}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center mb-3">
          <div>
            <div className="text-3xl font-bold text-plum-700">{c.scanned_count}</div>
            <div className="text-[11px] text-gray-500">scanned</div>
          </div>
          <div>
            <div className="text-3xl font-bold text-gray-600">{c.expected_count}</div>
            <div className="text-[11px] text-gray-500">expected</div>
          </div>
          <div>
            <div className={`text-3xl font-bold ${(c.missing?.length || 0) > 0 ? 'text-red-700' : 'text-gray-400'}`}>
              {c.missing?.length || 0}
            </div>
            <div className="text-[11px] text-gray-500">missing so far</div>
          </div>
        </div>
        <form onSubmit={e => { e.preventDefault(); if (draft.trim()) scan.mutate(draft.trim()) }}>
          <div className="flex gap-1">
            <input ref={inputRef}
                   className="input text-sm flex-1 font-mono"
                   placeholder="Scan / type our_id (e.g. WWC0700)"
                   value={draft}
                   onChange={e => setDraft(e.target.value)}
                   autoFocus />
            <button type="submit" className="btn-primary text-sm flex items-center gap-1"
                    disabled={!draft.trim() || scan.isPending}>
              <Check size={13} /> Scan
            </button>
            <button type="button"
                    onClick={() => setCameraOpen(true)}
                    className="btn-secondary text-sm flex items-center gap-1"
                    title="Use phone/laptop camera to scan QR labels">
              <Camera size={13} /> Camera
            </button>
          </div>
        </form>
        {lastResult && Date.now() - lastResult.at < 4000 && (
          <div className={`mt-2 text-[11px] rounded px-2 py-1 ${
            lastResult.ok
              ? 'bg-green-50 border border-green-200 text-green-800'
              : 'bg-red-50 border border-red-200 text-red-800'
          }`}>
            {lastResult.ok
              ? <>✓ Scanned <span className="font-mono">{lastResult.our_id}</span></>
              : <>✗ {lastResult.msg} {lastResult.raw && <span className="font-mono opacity-70">({lastResult.raw})</span>}</>}
          </div>
        )}
      </div>

      {cameraOpen && (
        <CameraScanner
          onClose={() => setCameraOpen(false)}
          onDetect={(code) => scan.mutate(code)}
          alreadyScanned={new Set((c.missing || []).map(d => d.id))} />
      )}

      {c.unexpected?.length > 0 && (
        <div className="card !p-3 bg-amber-50 border border-amber-200">
          <div className="flex items-center gap-1.5 mb-1">
            <AlertTriangle size={14} className="text-amber-700" />
            <h3 className="text-sm font-semibold text-amber-900">
              Unexpected scans ({c.unexpected.length})
            </h3>
            <span className="text-[11px] text-amber-700">— device wasn't expected at this location</span>
          </div>
          <ul className="text-xs space-y-0.5">
            {c.unexpected.map(d => (
              <li key={d.id} className="flex items-baseline gap-2">
                <span className="font-mono">{d.our_id}</span>
                <span className="text-gray-600">— {d.device_type_name}</span>
                <span className="text-[10px] text-gray-500">@ {d.location}</span>
                <span className="text-[10px] uppercase ml-auto">{d.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {c.missing?.length > 0 && (
        <div className="card !p-3">
          <h3 className="text-sm font-semibold text-gray-800 mb-1">Not Yet Scanned ({c.missing.length})</h3>
          <ul className="text-xs space-y-0.5 max-h-72 overflow-y-auto">
            {c.missing.map(d => (
              <li key={d.id} className="flex items-baseline gap-2">
                <span className="font-mono">{d.our_id}</span>
                <span className="text-gray-600">— {d.device_type_name}</span>
                <span className="text-[10px] text-gray-500">@ {d.location}</span>
                <span className="text-[10px] uppercase ml-auto">{d.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card !p-3">
        <div className="flex items-center gap-2 mb-2">
          <input className="input text-sm flex-1" placeholder="Notes (optional)"
                 value={notes} onChange={e => setNotes(e.target.value)} />
          <button className="btn-primary text-sm"
                  onClick={() => {
                    if (c.missing?.length && !confirm(
                      `Finish count? ${c.missing.length} device(s) will be marked LOST. Continue?`
                    )) return
                    finish.mutate()
                  }}
                  disabled={finish.isPending}>
            {finish.isPending ? 'Finishing…' :
              c.missing?.length ? `Finish (mark ${c.missing.length} lost)` : 'Finish'}
          </button>
        </div>
      </div>
    </div>
  )
}


function CameraScanner({ onClose, onDetect, alreadyScanned }) {
  const scannerRef = useRef(null)
  const containerRef = useRef(null)
  const [status, setStatus] = useState('starting')   // starting | active | error
  const [error, setError] = useState(null)
  const lastDecodeRef = useRef({ text: '', at: 0 })

  useEffect(() => {
    let cancelled = false
    async function startScanner() {
      try {
        const elementId = 'larc-camera-scanner'
        const html5Qr = new Html5Qrcode(elementId, { verbose: false })
        scannerRef.current = html5Qr
        await html5Qr.start(
          { facingMode: 'environment' },   // rear camera on phones; webcam on laptops
          {
            fps: 10,
            qrbox: (w, h) => {
              const side = Math.min(w, h) * 0.7
              return { width: side, height: side }
            },
            aspectRatio: 1.333,
          },
          (decodedText) => {
            // Debounce: ignore the same code within 1.5s
            const now = Date.now()
            if (lastDecodeRef.current.text === decodedText
                && now - lastDecodeRef.current.at < 1500) return
            lastDecodeRef.current = { text: decodedText, at: now }
            onDetect(decodedText)
          },
          () => {},   // scan error per frame — ignore
        )
        if (!cancelled) setStatus('active')
      } catch (err) {
        if (!cancelled) {
          setStatus('error')
          setError(err?.message || 'Could not start camera. Grant permission?')
        }
      }
    }
    startScanner()
    return () => {
      cancelled = true
      const s = scannerRef.current
      if (s && s.isScanning) {
        s.stop().then(() => s.clear()).catch(() => {})
      }
    }
  }, [onDetect])

  return (
    <div className="fixed inset-0 z-50 bg-black/85 flex flex-col">
      <div className="bg-white px-4 py-3 flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-sm">Camera scanner</h3>
          <p className="text-[11px] text-gray-500">
            Point at a label's QR code. Multiple scans queue automatically.
          </p>
        </div>
        <button onClick={onClose}
                className="btn-secondary text-sm flex items-center gap-1">
          <X size={14} /> Close
        </button>
      </div>
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div id="larc-camera-scanner" ref={containerRef}
               className="rounded overflow-hidden bg-black" />
          {status === 'starting' && (
            <div className="text-white text-center text-sm mt-3">Starting camera…</div>
          )}
          {status === 'error' && (
            <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-3 rounded mt-3">
              <strong>Camera blocked.</strong> {error}
              <ul className="text-[11px] mt-1 list-disc pl-5">
                <li>On iPhone: tap the page URL → AA icon → "Website Settings" → Camera = Allow</li>
                <li>On Android: tap the lock icon in the address bar → Permissions → Camera</li>
                <li>Page must be served over HTTPS (or localhost) — http:// won't get camera access</li>
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function beep(freq = 880, durationMs = 80) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext
  if (!AudioCtx) return
  const ctx = new AudioCtx()
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.connect(gain); gain.connect(ctx.destination)
  osc.frequency.value = freq
  osc.type = 'sine'
  gain.gain.value = 0.15
  osc.start()
  setTimeout(() => {
    osc.stop()
    ctx.close().catch(() => {})
  }, durationMs)
}


function HistoryTable({ history, onOpen }) {
  if (history.length === 0) {
    return <div className="card text-xs text-gray-400 italic">No counts yet.</div>
  }
  return (
    <div className="card !p-0 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-plum-50">
          <tr>
            <th className="table-th">Started</th>
            <th className="table-th">By</th>
            <th className="table-th">Scope</th>
            <th className="table-th">Scanned</th>
            <th className="table-th">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {history.map(c => (
            <tr key={c.id} className="hover:bg-plum-50/30 cursor-pointer"
                onClick={() => onOpen(c.id)}>
              <td className="table-td text-[11px]">{fmt.date(c.started_at.slice(0, 10))}</td>
              <td className="table-td text-[11px]">{c.started_by?.split('@')[0]}</td>
              <td className="table-td text-[11px]">{c.scope_location || 'all'}</td>
              <td className="table-td text-[11px]">
                {c.scanned_count} / {c.expected_count}
              </td>
              <td className="table-td">
                <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
                  c.status === 'reconciled' ? 'bg-green-100 text-green-700' :
                  c.status === 'in_progress' ? 'bg-amber-100 text-amber-700' :
                  'bg-gray-100 text-gray-700'
                }`}>{c.status.replace('_', ' ')}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
