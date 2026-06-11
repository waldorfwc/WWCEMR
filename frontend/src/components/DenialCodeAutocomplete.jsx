import { useState, useRef, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import api from '../utils/api'
import { GROUP_CODES } from './DenialCodeDrawer'

/**
 * Slim autocomplete to look up a CARC/RARC code from scratch (no denial
 * context). Designed to sit in the Denials page header.
 *
 *   <DenialCodeAutocomplete onPick={(req) => openDrawer(req)} />
 *
 * onPick receives a drawer request object:
 *   { mode: 'single', code_type: 'CARC'|'RARC', code: '197' }
 */
export default function DenialCodeAutocomplete({ onPick }) {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const inputRef = useRef(null)

  const { data } = useQuery({
    queryKey: ['adjustment-codes-typeahead-header', q],
    queryFn: () => api.get('/adjustment-codes', {
      params: { q: q || undefined, per_page: 8 },
    }).then(r => r.data),
    enabled: focused,
    staleTime: 60_000,
  })
  const codeItems = data?.items || []

  // Surface group-code matches (CO, PR, OA, PI, CR) at the top of the list
  // whenever the query looks like it starts with / is a group code.
  const groupMatches = useMemo(() => {
    const qTrim = q.trim().toUpperCase()
    if (!qTrim) return []
    return Object.keys(GROUP_CODES).filter(k => k.startsWith(qTrim) || qTrim.startsWith(k))
  }, [q])

  // Combined list for keyboard nav: groups first, then codes.
  const items = useMemo(() => [
    ...groupMatches.map(g => ({ kind: 'group', key: `group-${g}`, group: g })),
    ...codeItems.map(c => ({ kind: 'code', key: `${c.code_type}-${c.code}`, ...c })),
  ], [groupMatches, codeItems])

  useEffect(() => { setActiveIdx(0) }, [q, items.length])

  function pick(item) {
    if (item.kind === 'group') {
      onPick({ mode: 'group', group_code: item.group })
    } else {
      onPick({ mode: 'single', code_type: item.code_type, code: item.code })
    }
    setQ('')
    inputRef.current?.blur()
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      // Prefer exact code match (either group or CARC/RARC) if the user typed one.
      const qt = q.trim().toUpperCase()
      const exact = items.find(it =>
        (it.kind === 'group' && it.group === qt) ||
        (it.kind === 'code' && it.code.toUpperCase() === qt)
      )
      const choice = exact || items[activeIdx]
      if (choice) pick(choice)
    } else if (e.key === 'Escape') {
      setQ('')
      inputRef.current?.blur()
    }
  }

  return (
    <div className="relative w-[260px]">
      <Search size={13} className="absolute left-2.5 top-2 text-gray-400" />
      <input
        ref={inputRef}
        className="w-full pl-7 pr-2 py-1.5 border border-border-subtle rounded text-xs focus:outline-none focus:ring-1 focus:ring-plum-400"
        placeholder="Look up a denial code… (e.g. 197, M86)"
        value={q}
        onChange={e => setQ(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 150)}
      />
      {focused && items.length > 0 && (
        <div className="absolute left-0 right-0 mt-1 max-h-80 overflow-y-auto bg-white border border-border-subtle rounded shadow-lg z-30">
          {items.map((row, i) => (
            <button
              key={row.key}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(row)}
              onMouseEnter={() => setActiveIdx(i)}
              className={`w-full text-left px-2.5 py-1.5 text-xs border-b border-border-subtle last:border-0 ${
                i === activeIdx ? 'bg-plum-100' : 'hover:bg-plum-50'
              }`}
            >
              {row.kind === 'group' ? (
                <>
                  <span className="font-mono font-bold text-red-700">GROUP {row.group}</span>
                  <span className="text-gray-700 ml-2">
                    {GROUP_CODES[row.group]?.name} — {GROUP_CODES[row.group]?.short}
                  </span>
                </>
              ) : (
                <>
                  <span className="font-mono font-semibold text-plum-600">
                    {row.code_type} {row.code}
                  </span>
                  <span className="text-gray-700 ml-2">
                    {row.official_verbiage.slice(0, 80)}
                    {row.official_verbiage.length > 80 ? '…' : ''}
                  </span>
                </>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
