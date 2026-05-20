import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

/**
 * Styled confirmation dialog + async hook.
 *
 *   const confirm = useConfirm()
 *   if (!await confirm({ title: 'Delete preset?', message: '…', danger: true })) return
 *
 * Drop-in replacement for window.confirm: returns a Promise<boolean>.
 * Wrap the app once in <ConfirmProvider> (see App.jsx).
 */

const ConfirmContext = createContext(null)

export function useConfirm() {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used within <ConfirmProvider>')
  return ctx
}

export function ConfirmProvider({ children }) {
  const [opts, setOpts] = useState(null)
  const resolverRef = useRef(null)

  const confirm = useCallback((options) => {
    setOpts({
      title:        options.title        || 'Are you sure?',
      message:      options.message      || '',
      confirmLabel: options.confirmLabel || 'Confirm',
      cancelLabel:  options.cancelLabel  || 'Cancel',
      danger:       options.danger ?? true,
    })
    return new Promise((resolve) => { resolverRef.current = resolve })
  }, [])

  const settle = useCallback((result) => {
    setOpts(null)
    if (resolverRef.current) {
      resolverRef.current(result)
      resolverRef.current = null
    }
  }, [])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && (
        <ConfirmDialog
          {...opts}
          onConfirm={() => settle(true)}
          onCancel={() => settle(false)}
        />
      )}
    </ConfirmContext.Provider>
  )
}

function ConfirmDialog({ title, message, confirmLabel, cancelLabel, danger, onConfirm, onCancel }) {
  const confirmBtnRef = useRef(null)

  useEffect(() => {
    confirmBtnRef.current?.focus()
    const onKey = (e) => {
      if (e.key === 'Escape') onCancel()
      else if (e.key === 'Enter') onConfirm()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onConfirm, onCancel])

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60] p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel() }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        className="bg-white rounded-lg border border-border-subtle w-[440px] max-w-full p-5"
      >
        <h2 className="font-serif text-lg text-ink m-0">{title}</h2>
        {message && (
          <div className="text-[13px] text-muted mt-2 whitespace-pre-line">{message}</div>
        )}
        <div className="mt-5 flex gap-2 justify-end">
          <button className="btn-secondary" onClick={onCancel}>{cancelLabel}</button>
          <button
            ref={confirmBtnRef}
            className={danger
              ? 'text-sm px-3 py-1.5 rounded text-white bg-red-700 hover:bg-red-800'
              : 'btn-primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
