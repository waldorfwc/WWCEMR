/**
 * HelpPanel — a right-anchored slide-over that explains a page's controls and
 * workflow. Matches the app's drawer pattern (fixed overlay + backdrop +
 * right panel, sticky header, Esc / backdrop-click to close).
 *
 * Renders, for a HELP_CONTENT entry:
 *   • a numbered step-strip for the workflow (wraps responsively)
 *   • one icon'd, color-toned section per major control/area
 *   • optional tip callouts at the bottom
 */
import { useEffect } from 'react'
import { X } from 'lucide-react'
import { TONES } from './helpContent'

// Circled-number glyphs for the step strip (1–10 covered; falls back to plain).
const CIRCLED = ['①','②','③','④','⑤','⑥','⑦','⑧','⑨','⑩']
const stepGlyph = (i) => CIRCLED[i] || `(${i + 1})`

export default function HelpPanel({ content, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!content) return null
  const { title, steps = [], sections = [], tips = [] } = content

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div
        className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Help: ${title}`}
      >
        {/* Sticky header */}
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[16px]">
            Help · {title}
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Close help">
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* Step strip */}
          {steps.length > 0 && (
            <div className="bg-plum-50/70 border border-plum-100 rounded-md px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] text-plum-900">
                {steps.map((step, i) => (
                  <span key={step} className="flex items-center gap-1.5">
                    <span className="flex items-center gap-1">
                      <span className="text-plum-600 text-[14px] leading-none">{stepGlyph(i)}</span>
                      <span className="font-medium">{step}</span>
                    </span>
                    {i < steps.length - 1 && (
                      <span className="text-plum-300" aria-hidden="true">→</span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Sections */}
          <div className="space-y-3">
            {sections.map((s, i) => {
              const Icon = s.icon
              const tone = TONES[s.tone] || TONES.gray
              return (
                <div key={s.title + i} className="flex gap-3">
                  <div className={`shrink-0 w-8 h-8 rounded-md flex items-center justify-center ${tone.chip}`}>
                    {Icon ? <Icon size={16} /> : null}
                  </div>
                  <div className="min-w-0">
                    <div className="font-medium text-ink text-[13px] leading-tight">{s.title}</div>
                    <p className="text-[12px] text-muted leading-snug mt-0.5">{s.body}</p>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Tips */}
          {tips.length > 0 && (
            <div className="space-y-2 pt-1">
              {tips.map((tip, i) => (
                <div
                  key={i}
                  className={`text-[12px] rounded-md border px-3 py-2 ${TONES.amber.callout}`}
                >
                  <span className="font-semibold">Tip · </span>{tip}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
