import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Image, CheckCircle2 } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

export default function PelletMammo() {
  const qc = useQueryClient()
  const [file, setFile] = useState(null)
  const [err, setErr] = useState('')

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append('file', file)
      return pelletPortalApi.post('/mammo', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-dashboard'] }),
    onError: (e) => {
      const detail = e?.response?.data?.detail
      setErr(typeof detail === 'string' ? detail : 'Upload failed. Please try again.')
    },
  })

  const done = upload.isSuccess

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Upload Your Mammogram
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Upload a copy of your most recent mammogram report. Our staff will
          review it before your insertion.
        </p>
      </header>

      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
        <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 mb-4">
          <Image size={20} />
        </div>

        {done ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-[14px] text-emerald-800 bg-emerald-50
                              border border-emerald-200 rounded-lg px-4 py-3">
              <CheckCircle2 size={16} />
              Submitted — awaiting staff review.
            </div>
            <Link to="/pellet-portal/home" className="btn-secondary text-sm inline-block">
              Back to Checklist
            </Link>
          </div>
        ) : (
          <>
            <div className="text-xs text-plum-600/80 mb-2">
              PDF, JPEG, PNG, or HEIC.
            </div>
            <input type="file"
                   accept="application/pdf,image/jpeg,image/png,image/heic"
                   onChange={e => { setErr(''); setFile(e.target.files?.[0] || null) }}
                   className="text-sm" />
            <div className="mt-5 flex items-center gap-3">
              <button onClick={() => { setErr(''); upload.mutate() }}
                      disabled={!file || upload.isPending}
                      className="btn-primary text-sm">
                {upload.isPending ? 'Uploading…' : 'Upload'}
              </button>
              <Link to="/pellet-portal/home"
                    className="text-[12px] text-plum-700 hover:text-plum-900 underline">
                Back to Checklist
              </Link>
            </div>
            {err && <div className="text-xs text-rose-700 mt-3">{err}</div>}
          </>
        )}
      </section>
    </div>
  )
}
