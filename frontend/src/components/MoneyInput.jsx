import React from 'react'

/**
 * Money input with a $ prefix and 2-decimal step.
 *
 * Props:
 * - value: number | string | null/undefined
 * - onChange: (newValue: string) => void
 * - disabled?: boolean
 * - placeholder?: string
 * - className?: string — additional classes merged onto the <input>
 */
export default function MoneyInput({ value, onChange, disabled, placeholder, className = '' }) {
  const displayValue = value === null || value === undefined ? '' : String(value)
  return (
    <div className="relative">
      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-muted text-[12px] pointer-events-none">$</span>
      <input
        type="number"
        step="0.01"
        inputMode="decimal"
        className={`input w-full pl-5 py-1 text-[12px] font-mono ${className}`}
        value={displayValue}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
      />
    </div>
  )
}
