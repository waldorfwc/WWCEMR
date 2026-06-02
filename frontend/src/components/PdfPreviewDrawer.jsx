import { useState, useEffect, useMemo } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import { X, Download, ZoomIn, ZoomOut, RotateCw } from 'lucide-react'
import api from '../utils/api'

// Worker source — same pattern as InsuranceDocuments.jsx
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()


/**
 * Reusable PDF preview drawer. Fetches the PDF bytes through the
 * authenticated axios client so signed-URL / bearer-token flows work.
 *
 * Props:
 *   apiPath  — path on the backend that streams the PDF bytes,
 *              e.g. '/surgery/<id>/files/<file_id>/download'.
 *              Must start with '/' (no '/api' prefix).
 *   filename — used for the in-drawer title + as the download filename
 *              when the user clicks Download.
 *   title    — optional drawer header. Defaults to filename.
 *   onClose  — called when the drawer is dismissed.
 */
export default function PdfPreviewDrawer({ apiPath, filename, title, onClose }) {
  const [pdfData, setPdfData] = useState(null)
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1)
  const [rotation, setRotation] = useState(0)
  const [error, setError] = useState(null)

  // Stable file prop — see InsuranceDocuments.jsx for why this matters
  // (pdf.js detaches the ArrayBuffer; a new object literal causes a reload
  // that fails because the buffer is gone).
  const fileProp = useMemo(() => (pdfData ? { data: pdfData } : null), [pdfData])

  useEffect(() => {
    let alive = true
    setPdfData(null); setError(null); setNumPages(0)
    api.get(apiPath, { responseType: 'arraybuffer' })
       .then(res => {
         if (alive) setPdfData(new Uint8Array(res.data))
       })
       .catch(err => {
         if (!alive) return
         setError(
           err?.response?.data?.detail
           || err?.message
           || 'Failed to load PDF.'
         )
       })
    return () => { alive = false }
  }, [apiPath])

  function downloadFile() {
    if (!pdfData) return
    const blob = new Blob([pdfData], { type: 'application/pdf' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename || 'document.pdf'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-full max-w-4xl bg-white shadow-xl flex flex-col"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between gap-3 z-10">
          <h2 className="font-serif font-semibold text-ink text-[15px] flex-1 truncate">
            {title || filename || 'Preview'}
          </h2>
          <div className="flex items-center gap-1">
            <button onClick={() => setScale(s => Math.max(0.5, +(s - 0.25).toFixed(2)))}
                    className="p-1 text-gray-600 hover:text-plum-700"
                    title="Zoom out"><ZoomOut size={16} /></button>
            <span className="text-[11px] text-gray-500 w-10 text-center">
              {Math.round(scale * 100)}%
            </span>
            <button onClick={() => setScale(s => Math.min(3, +(s + 0.25).toFixed(2)))}
                    className="p-1 text-gray-600 hover:text-plum-700"
                    title="Zoom in"><ZoomIn size={16} /></button>
            <button onClick={() => setRotation(r => (r + 90) % 360)}
                    className="p-1 text-gray-600 hover:text-plum-700"
                    title="Rotate 90°"><RotateCw size={16} /></button>
            <button onClick={downloadFile} disabled={!pdfData}
                    className="btn-secondary text-xs flex items-center gap-1 ml-2">
              <Download size={12} /> Download
            </button>
            <button onClick={onClose}
                    className="text-muted hover:text-ink ml-1"
                    title="Close (Esc)"><X size={18} /></button>
          </div>
        </div>
        <div className="flex-1 overflow-auto bg-gray-100 p-4">
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">
              {error}
            </div>
          )}
          {!fileProp && !error && (
            <div className="text-sm text-gray-400 italic text-center py-8">
              Loading PDF…
            </div>
          )}
          {fileProp && (
            <div className="flex justify-center">
              <Document
                file={fileProp}
                onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                onLoadError={(e) => setError(e?.message || 'Unable to render PDF.')}
                loading={<div className="text-sm text-gray-400 italic">Rendering…</div>}
              >
                <div className="space-y-4">
                  {Array.from({ length: numPages || 0 }, (_, i) => (
                    <div key={i + 1} className="shadow-md bg-white">
                      <Page pageNumber={i + 1}
                            scale={scale}
                            rotate={rotation}
                            renderAnnotationLayer={false}
                            renderTextLayer={false} />
                    </div>
                  ))}
                </div>
              </Document>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
