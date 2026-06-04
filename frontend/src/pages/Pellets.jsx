import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  Pill, Truck, AlertTriangle, Clock, ArrowRightLeft, Plus, X, Save,
  CheckCircle2, ClipboardList, BookOpen, FileText, Shield, Trash2,
  DollarSign, Paperclip, Upload, Edit3, ShoppingCart, Wrench,
  Download, Printer,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'


const LOC_LABEL = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
}


export default function Pellets() {
  const [receiveOpen, setReceiveOpen] = useState(false)
  const [transferOpen, setTransferOpen] = useState(false)
  const [transferPrefill, setTransferPrefill] = useState(null)
  const [disposeOpen, setDisposeOpen] = useState(false)
  const [placeOrderOpen, setPlaceOrderOpen] = useState(false)
  const [orderDetailId, setOrderDetailId] = useState(null)
  const [receivePrefillOrderId, setReceivePrefillOrderId] = useState(null)

  const { data: dash } = useQuery({
    queryKey: ['pellet-dashboard'],
    queryFn: () => api.get('/pellets/dashboard').then(r => r.data),
  })
  const { data: types } = useQuery({
    queryKey: ['pellet-dose-types'],
    queryFn: () => api.get('/pellets/dose-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: ordersResp } = useQuery({
    queryKey: ['pellet-orders-recent'],
    queryFn: () => api.get('/pellets/orders?limit=5').then(r => r.data),
  })
  const recentOrders = ordersResp?.orders || []

  return (
    <div>
      {/* Header */}
      <div className="flex items-baseline justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Pill size={22} className="text-plum-700" />
            Pellet inventory
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            <span className="text-plum-700 font-medium">Estradiol</span> +
            {' '}<span className="text-amber-700 font-medium">Testosterone</span>
            {' '}<Shield size={11} className="inline text-amber-700" /> Schedule III ·
            ordered from Qualgen · stored in the double-locked box at White Plains.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <Link to="/pellets/patients" className="btn-secondary text-sm flex items-center gap-1">
            <ClipboardList size={13}/> Patients
          </Link>
          <Link to="/pellets/counts" className="btn-secondary text-sm flex items-center gap-1">
            <ClipboardList size={13}/> Daily count
          </Link>
          <Link to="/pellets/audit" className="btn-secondary text-sm flex items-center gap-1">
            <FileText size={13}/> Audit log
          </Link>
          <Link to="/pellets/manual" className="btn-secondary text-sm flex items-center gap-1">
            <BookOpen size={13}/> Manual
          </Link>
          <button className="btn-secondary text-sm flex items-center gap-1"
                   onClick={() => setDisposeOpen(true)}>
            <Trash2 size={13}/> Dispose
          </button>
          <button className="btn-secondary text-sm flex items-center gap-1"
                   onClick={() => setTransferOpen(true)}>
            <ArrowRightLeft size={13}/> Transfer
          </button>
          <button className="btn-secondary text-sm flex items-center gap-1"
                   onClick={() => setPlaceOrderOpen(true)}>
            <ShoppingCart size={13}/> Place order
          </button>
          <button className="btn-primary text-sm flex items-center gap-1"
                   onClick={() => setReceiveOpen(true)}>
            <Plus size={13}/> Receive shipment
          </button>
        </div>
      </div>

      <InventoryLockBanner />

      {/* Daily count blockers — surface BEFORE everything else so the
          MA sees blockers without clicking into Counts. */}
      {(dash?.count_blockers_by_location?.total ?? 0) > 0 && (
        <CountBlockerBanner blockers={dash.count_blockers_by_location} />
      )}

      {/* On-hand by hormone × location */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <HormoneCard title="Estradiol" tone="plum"
                      counts={dash?.on_hand_by_hormone_location?.estradiol} />
        <HormoneCard title="Testosterone (Schedule III)" tone="amber"
                      counts={dash?.on_hand_by_hormone_location?.testosterone} />
      </div>

      {/* Reorder + expiring alerts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <Truck size={14} className="text-amber-700" />
            <h2 className="text-sm font-semibold text-gray-800">Reorder alerts</h2>
            <span className="text-[11px] text-muted">(packs ≤ threshold)</span>
          </div>
          {(dash?.reorder_alerts || []).length === 0 ? (
            <div className="text-xs text-gray-400 italic">All doses are above threshold.</div>
          ) : (
            <ul className="text-xs space-y-1">
              {dash.reorder_alerts.map((r, i) => (
                <li key={`${r.dose_type_id}-${r.location || 'global'}-${i}`}
                     className="flex items-baseline justify-between bg-amber-50 px-2 py-1 rounded">
                  <span>
                    <strong>{r.label}</strong>
                    {r.is_controlled && <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>}
                    {r.location && (
                      <span className="ml-1 text-[10px] uppercase px-1 rounded bg-plum-100 text-plum-800">
                        @ {LOC_LABEL[r.location] || r.location}
                      </span>
                    )}
                  </span>
                  <span className="text-amber-700">
                    {r.on_hand_packs} packs ({r.on_hand_doses} doses) · order {r.order_qty_packs ?? '?'} packs
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <Clock size={14} className="text-red-700" />
            <h2 className="text-sm font-semibold text-gray-800">Expiring within 90 days</h2>
          </div>
          {(dash?.expiring_soon || []).length === 0 ? (
            <div className="text-xs text-gray-400 italic">No lots expiring in the next 90 days.</div>
          ) : (
            <ul className="text-xs space-y-1">
              {dash.expiring_soon.slice(0, 8).map(l => (
                <li key={l.lot_id} className="flex items-baseline justify-between px-1 py-0.5 rounded">
                  <span><strong>{l.label}</strong> <span className="text-gray-500 font-mono">lot {l.qualgen_lot}</span></span>
                  <span className={l.days_to_expiry < 30 ? 'text-red-700 font-semibold' : 'text-amber-700'}>
                    {l.days_to_expiry}d · {l.doses_on_hand} doses
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Ordered Pellets — recent orders + late-shipment flags */}
      <OrdersCard orders={recentOrders}
                    lateOrders={dash?.late_orders || []}
                    inTransitOrders={dash?.open_orders || []}
                    onPlace={() => setPlaceOrderOpen(true)}
                    onOpenDetail={(id) => setOrderDetailId(id)}
                    onReceiveAgainst={(id) => {
                      setReceivePrefillOrderId(id)
                      setReceiveOpen(true)
                    }} />

      {/* Open transfers + counts */}
      {((dash?.open_transfers?.length ?? 0) > 0 || (dash?.open_counts?.length ?? 0) > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
          {(dash?.open_transfers?.length ?? 0) > 0 && (
            <div className="card">
              <div className="flex items-center gap-1.5 mb-2">
                <ArrowRightLeft size={14} className="text-blue-700" />
                <h2 className="text-sm font-semibold text-gray-800">Transfers in Chain of Custody</h2>
              </div>
              {(dash?.transfers_awaiting_pickup?.length ?? 0) > 0 && (
                <>
                  <div className="text-[10px] uppercase text-amber-700 font-semibold mt-1 mb-1">
                    Awaiting courier pickup
                  </div>
                  <ul className="text-xs space-y-1 mb-2">
                    {dash.transfers_awaiting_pickup.map(t => (
                      <TransferRow key={t.id} t={t} />
                    ))}
                  </ul>
                </>
              )}
              {(dash?.transfers_in_transit?.length ?? 0) > 0 && (
                <>
                  <div className="text-[10px] uppercase text-blue-700 font-semibold mt-1 mb-1">
                    In transit (with courier)
                  </div>
                  <ul className="text-xs space-y-1">
                    {dash.transfers_in_transit.map(t => (
                      <TransferRow key={t.id} t={t} />
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          {(dash?.open_counts?.length ?? 0) > 0 && (
            <div className="card">
              <div className="flex items-center gap-1.5 mb-2">
                <ClipboardList size={14} className="text-violet-700" />
                <h2 className="text-sm font-semibold text-gray-800">Open counts</h2>
              </div>
              <ul className="text-xs space-y-1">
                {dash.open_counts.map(c => (
                  <li key={c.id} className="flex items-baseline justify-between bg-violet-50 px-2 py-1 rounded">
                    <span>{LOC_LABEL[c.location] || c.location} · started by {c.started_by?.split('@')[0]}</span>
                    <Link to={`/pellets/counts/${c.id}`} className="text-plum-700 hover:underline">
                      {c.lines_remaining} lots remaining →
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Lots in inventory — edit lot numbers / expirations */}
      <LotsCard types={types || []}
                  onQuickTransfer={(prefill) => {
                    setTransferPrefill(prefill)
                    setTransferOpen(true)
                  }} />

      {/* Dose-type catalog table */}
      <div className="card !p-0 overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 bg-plum-50 border-b border-plum-100">
          <h2 className="text-sm font-semibold text-gray-800">Dose-type catalog</h2>
          <Link to="/pellets/dose-types" className="text-[11px] text-plum-700 hover:underline">
            Edit thresholds →
          </Link>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="table-th">Dose</th>
              <th className="table-th text-right">On hand</th>
              <th className="table-th text-right">Reorder ≤</th>
              <th className="table-th text-right">Order qty</th>
              <th className="table-th">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(types || []).map(t => (
              <tr key={t.id}>
                <td className="table-td">
                  {t.label}
                  {t.is_controlled && (
                    <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
                  )}
                </td>
                <td className="table-td text-right font-mono text-[11px]">
                  {t.on_hand_packs} packs · {t.on_hand_doses} doses
                </td>
                <td className="table-td text-right text-[11px]">{t.reorder_threshold_packs} packs</td>
                <td className="table-td text-right text-[11px]">{t.reorder_qty_packs ?? '—'} packs</td>
                <td className="table-td text-[11px] text-gray-500">{t.notes || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {receiveOpen && (
        <ReceiveDrawer types={types || []}
                          prefillOrderId={receivePrefillOrderId}
                          onClose={() => {
                            setReceiveOpen(false)
                            setReceivePrefillOrderId(null)
                          }} />
      )}
      {transferOpen && (
        <TransferDrawer initial={transferPrefill}
                          onClose={() => {
                            setTransferOpen(false)
                            setTransferPrefill(null)
                          }} />
      )}
      {disposeOpen && <DisposeDrawer onClose={() => setDisposeOpen(false)} />}
      {placeOrderOpen && (
        <PlaceOrderDrawer types={types || []}
                             onClose={() => setPlaceOrderOpen(false)} />
      )}
      {orderDetailId && (
        <OrderDetailDrawer orderId={orderDetailId}
                              types={types || []}
                              onClose={() => setOrderDetailId(null)}
                              onReceive={(id) => {
                                setOrderDetailId(null)
                                setReceivePrefillOrderId(id)
                                setReceiveOpen(true)
                              }} />
      )}
    </div>
  )
}


function CountBlockerBanner({ blockers }) {
  const navigate = useNavigate()
  const locs = blockers?.locations || {}
  const total = blockers?.total || 0
  const entries = Object.entries(locs).filter(([, n]) => n > 0)
  return (
    <div className="card border border-red-200 bg-red-50/60 mb-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} className="text-red-700" />
          <h2 className="text-sm font-semibold text-red-800">
            Daily count blocked — {total} visit{total === 1 ? '' : 's'} pending confirmation
          </h2>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {entries.map(([loc, n]) => (
            <span key={loc} className="text-[11px] bg-white border border-red-200 px-1.5 py-0.5 rounded">
              <strong>{LOC_LABEL[loc] || loc}:</strong> {n}
            </span>
          ))}
          <button className="btn-secondary text-[11px] flex items-center gap-1"
                   onClick={() => navigate('/pellets/counts')}>
            Open Daily Counts →
          </button>
        </div>
      </div>
      <div className="text-[11px] text-red-700 mt-1">
        Confirm the dose card on each visit before starting a count. The count cannot
        reconcile inventory while proposed (planned/pulled) doses are waiting for
        provider sign-off. Click <strong>Open Daily Counts</strong> for the per-visit
        list and the one-click "Confirm as planned" action.
      </div>
    </div>
  )
}


function HormoneCard({ title, tone, counts }) {
  const cls = tone === 'amber'
    ? 'border-amber-100 bg-amber-50/40'
    : 'border-plum-100 bg-plum-50/30'
  const text = tone === 'amber' ? 'text-amber-700' : 'text-plum-700'
  const total = Object.values(counts || {}).reduce((a, b) => a + (b || 0), 0)
  return (
    <div className={`card border ${cls}`}>
      <div className={`text-[11px] uppercase tracking-wide ${text} font-semibold mb-2`}>{title}</div>
      <div className="grid grid-cols-3 gap-2">
        {Object.entries(LOC_LABEL).map(([k, l]) => (
          <div key={k} className="bg-white border border-gray-200 rounded p-2">
            <div className="text-[10px] uppercase text-gray-500">{l}</div>
            <div className="text-xl font-bold mt-0.5">{counts?.[k] ?? 0}</div>
            <div className="text-[10px] text-gray-400">doses</div>
          </div>
        ))}
      </div>
      <div className="text-[10px] text-gray-500 mt-1.5 text-right">
        Total: <strong>{total}</strong> doses
      </div>
    </div>
  )
}


// ─── Lots in inventory ─────────────────────────────────────────────

const ALL_LOCATION_COLS = ['white_plains', 'brandywine', 'arlington']
const LOC_SHORT = { white_plains: 'WP', brandywine: 'BR', arlington: 'AR' }


function LotsCard({ types = [], onQuickTransfer }) {
  const qc = useQueryClient()
  const [hormone, setHormone] = useState('')                  // '' | estradiol | testosterone
  const [locationFilter, setLocationFilter] = useState('')    // '' | white_plains | brandywine | arlington
  const [search, setSearch] = useState('')
  const [editingLot, setEditingLot] = useState(null)
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [showExpired, setShowExpired] = useState(false)

  // Look up reorder threshold + min pack size per dose-type
  const typeMeta = (() => {
    const out = {}
    for (const t of types) {
      const minPack = (t.pack_sizes && t.pack_sizes.length)
        ? Math.min(...t.pack_sizes) : 6
      const threshold_packs = t.reorder_threshold_packs ?? null
      out[t.id] = {
        threshold_packs,
        threshold_doses: threshold_packs != null ? threshold_packs * minPack : null,
        per_location:    t.reorder_thresholds_by_location || null,  // {loc: packs}
        min_pack:        minPack,
      }
    }
    return out
  })()

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-lots', hormone, search],
    queryFn: () => api.get('/pellets/lots', {
      params: {
        in_stock_only: true,
        ...(hormone ? { hormone } : {}),
        ...(search.trim() ? { search: search.trim() } : {}),
      },
    }).then(r => r.data),
  })
  const lots = data?.lots || []

  // Roll up by dose_type_id; preserve dose-type sort: estradiol asc → testosterone asc by mg.
  const groups = (() => {
    const by = new Map()
    for (const l of lots) {
      const key = l.dose_type_id
      if (!by.has(key)) {
        by.set(key, {
          dose_type_id:    l.dose_type_id,
          dose_type_label: l.dose_type_label || '(unknown)',
          hormone:         l.hormone,
          is_controlled:   !!l.is_controlled,
          lots:            [],
          total_on_hand:   0,
          balances:        { white_plains: 0, brandywine: 0, arlington: 0 },
        })
      }
      const g = by.get(key)
      g.lots.push(l)
      const total = Object.values(l.balances || {}).reduce((a, b) => a + (b || 0), 0)
      g.total_on_hand += total
      for (const [loc, n] of Object.entries(l.balances || {})) {
        g.balances[loc] = (g.balances[loc] || 0) + (n || 0)
      }
    }
    const all = Array.from(by.values()).sort((a, b) => {
      // estradiol before testosterone, then by label (which embeds mg)
      const h = (a.hormone || '').localeCompare(b.hormone || '')
      if (h !== 0) return h
      return a.dose_type_label.localeCompare(b.dose_type_label, undefined, { numeric: true })
    })
    // Apply the location filter: drop groups with 0 at the chosen location
    return locationFilter
      ? all.filter(g => (g.balances[locationFilter] || 0) > 0)
      : all
  })()

  const today = new Date(); today.setHours(0, 0, 0, 0)
  const todayIso = today.toISOString().slice(0, 10)

  // Split groups into Active vs Expired. A lot is expired when its
  // expiration_date is in the past; a group goes into "Expired" only when
  // ALL of its in-stock lots are expired. Groups with some-active +
  // some-expired stay in Active; the expired children are visually flagged.
  function isLotExpired(l) {
    return !!l.expiration_date && l.expiration_date < todayIso
  }
  const activeGroups = []
  const expiredGroups = []
  for (const g of groups) {
    const allExpired = g.lots.every(isLotExpired) && g.lots.length > 0
    if (allExpired) expiredGroups.push(g)
    else activeGroups.push(g)
  }

  function toggle(doseTypeId) {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(doseTypeId)) next.delete(doseTypeId)
      else next.add(doseTypeId)
      return next
    })
  }

  // Which location columns are visible. When filter is set, only that one.
  const LOCATION_COLS = locationFilter ? [locationFilter] : ALL_LOCATION_COLS
  const colTemplate = locationFilter
    ? 'grid-cols-[1fr_70px_70px_24px]'
    : 'grid-cols-[1fr_60px_60px_60px_70px_24px]'

  const allCollapsed = activeGroups.length > 0
    && activeGroups.every(g => collapsed.has(g.dose_type_id))

  function thresholdForLocation(doseTypeId, loc) {
    const meta = typeMeta[doseTypeId]
    if (!meta) return null
    if (meta.per_location && meta.per_location[loc] != null) {
      return meta.per_location[loc] * meta.min_pack
    }
    if (meta.threshold_doses != null && loc === 'white_plains') {
      // Global threshold defaults to WP (where stock is held); other
      // locations fall back to "no threshold" unless per-location is set.
      return meta.threshold_doses
    }
    return null
  }

  function isGroupAtThreshold(g) {
    const meta = typeMeta[g.dose_type_id]
    if (!meta || meta.threshold_doses == null) return false
    if (meta.per_location) {
      return LOCATION_COLS.some(loc => {
        const t = thresholdForLocation(g.dose_type_id, loc)
        return t != null && (g.balances[loc] || 0) <= t
      })
    }
    return g.total_on_hand <= meta.threshold_doses
  }

  function renderGroup(g, isExpiredSection) {
    const isCollapsed = collapsed.has(g.dose_type_id) && !isExpiredSection
    const sortedLots = [...g.lots].sort((a, b) =>
      (a.expiration_date || '').localeCompare(b.expiration_date || ''))
    const nextExp = sortedLots[0]?.expiration_date
    const nextExpDays = nextExp
      ? Math.round((new Date(nextExp + 'T00:00:00') - today) / 86400000)
      : null
    const lowStock = !isExpiredSection && isGroupAtThreshold(g)

    return (
      <li key={g.dose_type_id} className={lowStock ? 'bg-amber-50/40' : ''}>
        <button className={`w-full text-left grid ${colTemplate} gap-2 px-3 py-2 items-center
                            ${isExpiredSection ? 'hover:bg-red-50/60' : 'hover:bg-plum-50/30'}`}
                onClick={() => toggle(g.dose_type_id)}>
          <span className="flex items-center gap-2 min-w-0">
            <span className={`w-4 inline-block shrink-0 ${isExpiredSection ? 'text-red-700' : 'text-gray-400'}`}>
              {isCollapsed ? '▸' : '▾'}
            </span>
            <span className={`text-sm font-semibold truncate ${
              isExpiredSection ? 'text-red-800' : 'text-gray-800'
            }`}>
              {g.dose_type_label}
              {g.is_controlled && (
                <span className="ml-2 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
              )}
              <span className="ml-2 text-[11px] text-gray-500 font-normal">
                {g.lots.length} lot{g.lots.length === 1 ? '' : 's'}
              </span>
              {lowStock && (
                <span className="ml-2 text-[9px] uppercase px-1 py-[1px] rounded bg-amber-100 text-amber-800">
                  reorder
                </span>
              )}
            </span>
            {nextExpDays != null && (
              <span className={`text-[10px] ml-2 shrink-0 ${
                isExpiredSection ? 'text-red-700' :
                nextExpDays < 30 ? 'text-red-700' :
                nextExpDays < 90 ? 'text-amber-700' : 'text-gray-400'
              }`}>
                {isExpiredSection
                  ? `expired ${Math.abs(nextExpDays)}d ago`
                  : `next exp ${nextExpDays}d`}
              </span>
            )}
          </span>
          {LOCATION_COLS.map(loc => {
            const v = g.balances[loc] || 0
            const threshold = thresholdForLocation(g.dose_type_id, loc)
            const belowThreshold = !isExpiredSection && threshold != null && v <= threshold
            return (
              <span key={loc} className={`text-right font-mono text-[12px] ${
                belowThreshold ? 'text-amber-700 font-semibold' :
                v === 0 ? 'text-gray-300' : 'text-gray-700'
              }`}
                    title={threshold != null ? `Threshold ${threshold} doses` : undefined}>
                {v}
              </span>
            )
          })}
          <span className={`text-right font-mono text-sm font-semibold ${
            isExpiredSection ? 'text-red-800' : ''
          }`}>
            {locationFilter ? (g.balances[locationFilter] || 0) : g.total_on_hand}
          </span>
          <span></span>
        </button>

        {!isCollapsed && (
          <div className={`border-t ${
            isExpiredSection ? 'bg-red-50/30 border-red-100' : 'bg-gray-50/40 border-gray-100'
          }`}>
            {sortedLots
              // When a location filter is set, hide lots with 0 at that location
              .filter(l => !locationFilter || ((l.balances || {})[locationFilter] || 0) > 0)
              .map(l => {
              const expDate = l.expiration_date ? new Date(l.expiration_date + 'T00:00:00') : null
              const daysToExp = expDate ? Math.round((expDate - today) / 86400000) : null
              const isExpired = isLotExpired(l)
              return (
                <div key={l.id}
                     className={`grid ${colTemplate} gap-2 px-3 py-1.5 items-center
                                 border-b last:border-b-0 ${
                                   isExpired ? 'bg-red-50/30 border-red-100' : 'border-gray-100'
                                 }`}>
                  <div className="pl-7 flex items-baseline gap-2 min-w-0">
                    <span className="font-mono text-[12px] truncate">{l.qualgen_lot_number}</span>
                    <span className="text-[11px] text-gray-500 shrink-0">
                      exp {l.expiration_date ? fmt.date(l.expiration_date) : '—'}
                      {daysToExp != null && (
                        <span className={`ml-1 ${
                          isExpired ? 'text-red-700 font-semibold' :
                          daysToExp < 30 ? 'text-red-700' :
                          daysToExp < 90 ? 'text-amber-700' : 'text-gray-400'
                        }`}>
                          ({isExpired ? `expired ${Math.abs(daysToExp)}d ago` : `${daysToExp}d`})
                        </span>
                      )}
                    </span>
                  </div>
                  {LOCATION_COLS.map(loc => {
                    const v = (l.balances || {})[loc] || 0
                    if (v === 0) {
                      return (
                        <span key={loc} className="text-right font-mono text-[11px] text-gray-300">
                          0
                        </span>
                      )
                    }
                    return (
                      <button key={loc}
                              type="button"
                              className="text-right font-mono text-[11px] text-plum-700 hover:bg-plum-100/60 rounded px-1 cursor-pointer"
                              title={`Transfer from ${LOC_LABEL[loc]} →`}
                              disabled={isExpired}
                              onClick={(e) => {
                                e.stopPropagation()
                                if (isExpired) return
                                onQuickTransfer?.({
                                  lot_id: l.id,
                                  from_location: loc,
                                  max_doses: v,
                                })
                              }}>
                        {v}
                      </button>
                    )
                  })}
                  <span className="text-right font-mono text-[11px]">
                    {locationFilter
                      ? ((l.balances || {})[locationFilter] || 0)
                      : Object.values(l.balances || {}).reduce((a, b) => a + (b || 0), 0)}
                  </span>
                  <button className="text-plum-700 hover:underline text-[11px] inline-flex items-center"
                          onClick={(e) => { e.stopPropagation(); setEditingLot(l) }}
                          title="Edit lot">
                    <Edit3 size={11}/>
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </li>
    )
  }

  return (
    <div className="card mb-4 !p-0 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-plum-50 border-b border-plum-100 gap-2 flex-wrap">
        <div className="flex items-center gap-1.5">
          <ClipboardList size={14} className="text-plum-700" />
          <h2 className="text-sm font-semibold text-gray-800">Lots in Inventory</h2>
          <span className="text-[11px] text-muted">
            ({groups.length} dose{groups.length === 1 ? '' : 's'} · {lots.length} lot{lots.length === 1 ? '' : 's'})
          </span>
        </div>
        <div className="flex gap-2 items-center text-[11px]">
          <button className="text-plum-700 hover:underline text-[11px]"
                   onClick={() => setCollapsed(allCollapsed
                                                  ? new Set()
                                                  : new Set(groups.map(g => g.dose_type_id)))}>
            {allCollapsed ? 'Expand all' : 'Collapse all'}
          </button>
          <select className="input text-[11px] py-0.5" value={hormone}
                   onChange={e => setHormone(e.target.value)}>
            <option value="">Both hormones</option>
            <option value="estradiol">Estradiol</option>
            <option value="testosterone">Testosterone</option>
          </select>
          <select className="input text-[11px] py-0.5" value={locationFilter}
                   onChange={e => setLocationFilter(e.target.value)}>
            <option value="">All locations</option>
            <option value="white_plains">White Plains only</option>
            <option value="brandywine">Brandywine only</option>
            <option value="arlington">Arlington only</option>
          </select>
          <input className="input text-[11px] py-0.5" placeholder="Search lot #"
                  value={search}
                  onChange={e => setSearch(e.target.value)} />
          <ExportButtons hormone={hormone} location={locationFilter} search={search} />
        </div>
      </div>

      {/* Matrix column header — sticky inside the card body */}
      {!isLoading && (activeGroups.length > 0 || expiredGroups.length > 0) && (
        <div className={`sticky top-0 z-10 grid ${colTemplate} gap-2 px-3 py-1
                         bg-gray-100 text-[10px] uppercase text-gray-500 border-b border-gray-200 shadow-sm`}>
          <div>Dose / Lot</div>
          {LOCATION_COLS.map(loc => (
            <div key={loc} className="text-right">{LOC_SHORT[loc]}</div>
          ))}
          <div className="text-right font-semibold">Total</div>
          <div></div>
        </div>
      )}

      {isLoading && (
        <div className="text-center text-gray-400 py-6 text-sm">Loading…</div>
      )}
      {!isLoading && activeGroups.length === 0 && expiredGroups.length === 0 && (
        <div className="text-center text-gray-400 py-6 italic text-sm">No lots in stock.</div>
      )}

      <ul className="divide-y divide-gray-100">
        {activeGroups.map(g => renderGroup(g, false))}
      </ul>

      {/* Expired section (separate; collapsed by default) */}
      {expiredGroups.length > 0 && (
        <div className="border-t border-red-200 mt-2">
          <button className="w-full text-left px-3 py-2 bg-red-50/60 hover:bg-red-50 flex items-center gap-2 text-[12px]"
                  onClick={() => setShowExpired(s => !s)}>
            <span className="text-red-700 w-4 inline-block">{showExpired ? '▾' : '▸'}</span>
            <AlertTriangle size={12} className="text-red-700"/>
            <span className="font-semibold text-red-800">
              Expired (do not use) — {expiredGroups.length} dose{expiredGroups.length === 1 ? '' : 's'} ·
              {' '}{expiredGroups.reduce((a, g) => a + g.total_on_hand, 0)} doses on hand
            </span>
            <span className="ml-auto text-[10px] text-red-700">click to {showExpired ? 'hide' : 'show'}</span>
          </button>
          {showExpired && (
            <ul className="divide-y divide-red-100">
              {expiredGroups.map(g => renderGroup(g, true))}
            </ul>
          )}
        </div>
      )}

      {editingLot && (
        <EditLotDrawer lot={editingLot}
                          onClose={() => setEditingLot(null)}
                          onSaved={() => {
                            qc.invalidateQueries({ queryKey: ['pellet-lots'] })
                            qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
                            qc.invalidateQueries({ queryKey: ['pellet-dose-types'] })
                            setEditingLot(null)
                          }} />
      )}
    </div>
  )
}


function InventoryLockBanner() {
  const { has } = useCurrentUser()
  const canManage = !!has?.('pellet:manage')
  const { data } = useQuery({
    queryKey: ['pellet-inventory-lock'],
    queryFn: () => api.get('/pellets/settings/inventory-lock').then(r => r.data),
    staleTime: 30_000,
  })
  if (!data) return null
  return <InventoryLockCard state={data} canManage={canManage} />
}


function InventoryLockCard({ state, canManage }) {
  const qc = useQueryClient()
  const [pendingLock, setPendingLock] = useState(false)
  const [reason, setReason] = useState('')
  const [unlockConfirm, setUnlockConfirm] = useState(false)

  const setLock = useMutation({
    mutationFn: (body) => api.post('/pellets/settings/inventory-lock', body)
                            .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-inventory-lock'] })
      setPendingLock(false); setReason(''); setUnlockConfirm(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Failed to update lock'),
  })

  if (!state.locked && !canManage && !pendingLock) return null

  if (state.locked) {
    return (
      <div className="card mb-3 border-red-300 bg-red-50/50">
        <div className="flex items-baseline justify-between gap-2 flex-wrap">
          <div>
            <div className="text-sm font-semibold text-red-800 flex items-center gap-2">
              <Shield size={14}/> Pellet inventory is LOCKED
            </div>
            <div className="text-[12px] text-red-700 mt-0.5">
              Reason: {state.reason || '(none provided)'}
              {state.locked_by && <> · by {state.locked_by.split('@')[0]}</>}
              {state.locked_at && <> · {fmt.date(state.locked_at.slice(0,10))}</>}
            </div>
            <div className="text-[11px] text-gray-600 mt-1">
              Lot edits, dose-type catalog, and historical visit fix-ups are blocked.
              Normal flow (visits, bagging, returns, transfers, counts) is unaffected.
              Admins can override individual edits by providing a reason.
            </div>
          </div>
          {canManage && (
            !unlockConfirm ? (
              <button className="btn-secondary text-xs"
                       onClick={() => setUnlockConfirm(true)}>
                Unlock…
              </button>
            ) : (
              <div className="flex items-center gap-1">
                <button className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700"
                         onClick={() => setLock.mutate({ locked: false })}>
                  Confirm unlock
                </button>
                <button className="text-xs text-gray-500 hover:text-ink"
                         onClick={() => setUnlockConfirm(false)}>cancel</button>
              </div>
            )
          )}
        </div>
      </div>
    )
  }

  if (!canManage) return null
  return (
    <div className="card mb-3 border-gray-200">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <div>
          <div className="text-sm font-semibold text-gray-800 flex items-center gap-2">
            <Shield size={14} className="text-gray-500"/> Pellet inventory · unlocked
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5">
            Lock to freeze admin-style inventory edits (lots, dose types, historical visits) at go-live.
          </div>
        </div>
        {!pendingLock ? (
          <button className="btn-secondary text-xs"
                   onClick={() => setPendingLock(true)}>
            Lock inventory…
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <input className="input text-xs py-1 w-56"
                    placeholder="Reason (e.g. 'production go-live')"
                    value={reason}
                    onChange={e => setReason(e.target.value)}
                    autoFocus />
            <button className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                     disabled={!reason.trim() || setLock.isPending}
                     onClick={() => setLock.mutate({ locked: true, reason: reason.trim() })}>
              Lock
            </button>
            <button className="text-xs text-gray-500 hover:text-ink"
                     onClick={() => { setPendingLock(false); setReason('') }}>cancel</button>
          </div>
        )}
      </div>
    </div>
  )
}


function ExportButtons({ hormone, location, search }) {
  const [busy, setBusy] = useState(null)
  function exportParams() {
    const p = { in_stock_only: true }
    if (hormone)        p.hormone  = hormone
    if (location)       p.location = location
    if (search?.trim()) p.search   = search.trim()
    return p
  }
  async function fetchBlob(ext) {
    const res = await api.get(`/pellets/lots/export.${ext}`, {
      params: exportParams(),
      responseType: 'blob',
    })
    return res.data
  }
  async function downloadXlsx() {
    setBusy('xlsx')
    try {
      const blob = await fetchBlob('xlsx')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `pellet-inventory-${new Date().toISOString().slice(0,10)}.xlsx`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      alert(e?.response?.data?.detail || 'Excel export failed')
    } finally { setBusy(null) }
  }
  async function printPdf() {
    // Fetch the PDF with auth (axios attaches Bearer), then open the blob
    // in a new tab so the browser's print dialog can be triggered.
    setBusy('pdf')
    try {
      const blob = await fetchBlob('pdf')
      const url = URL.createObjectURL(blob)
      const win = window.open(url, '_blank', 'noopener')
      if (!win) {
        // Pop-up blocked — fall back to download
        const a = document.createElement('a')
        a.href = url
        a.download = `pellet-inventory-${new Date().toISOString().slice(0,10)}.pdf`
        document.body.appendChild(a); a.click(); a.remove()
      }
      setTimeout(() => URL.revokeObjectURL(url), 30_000)
    } catch (e) {
      alert(e?.response?.data?.detail || 'PDF export failed')
    } finally { setBusy(null) }
  }
  return (
    <>
      <button className="text-plum-700 hover:underline text-[11px] flex items-center gap-1 disabled:opacity-50"
              onClick={downloadXlsx} disabled={busy !== null}
              title="Download as Excel">
        <Download size={11}/> {busy === 'xlsx' ? '…' : 'Excel'}
      </button>
      <button className="text-plum-700 hover:underline text-[11px] flex items-center gap-1 disabled:opacity-50"
              onClick={printPdf} disabled={busy !== null}
              title="Open PDF (then print with Cmd+P)">
        <Printer size={11}/> {busy === 'pdf' ? '…' : 'Print PDF'}
      </button>
    </>
  )
}


function EditLotDrawer({ lot, onClose, onSaved }) {
  const [qualgenLot, setQualgenLot] = useState(lot.qualgen_lot_number || '')
  const [expDate, setExpDate]     = useState(lot.expiration_date || '')
  const [notes, setNotes]         = useState(lot.notes || '')
  const [reason, setReason]       = useState('')

  const save = useMutation({
    mutationFn: () => api.patch(`/pellets/lots/${lot.id}`, {
      qualgen_lot_number: qualgenLot.trim() || null,
      expiration_date:    expDate || null,
      notes:              notes,
      reason:             reason.trim(),
    }).then(r => r.data),
    onSuccess: onSaved,
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const canSave = reason.trim().length > 0 && (
    qualgenLot.trim() !== (lot.qualgen_lot_number || '') ||
    expDate !== (lot.expiration_date || '') ||
    notes !== (lot.notes || '')
  )

  return (
    <Drawer title={`Edit lot — ${lot.dose_type_label}`} onClose={onClose}>
      <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
        Use this to correct a placeholder ('made-up') lot number with the real
        Qualgen lot identifier, or fix an expiration typo. A reason is required
        — it lands in the audit log alongside the before/after values.
      </div>

      <div className="grid grid-cols-2 gap-2 text-[12px]">
        <div>
          <div className="text-[10px] uppercase text-gray-500">Dose</div>
          <div>{lot.dose_type_label}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Pack size</div>
          <div>{lot.pack_size ?? '—'}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Doses originally received</div>
          <div>{lot.doses_originally_received}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Received</div>
          <div>{lot.received_at ? fmt.date(lot.received_at.slice(0, 10)) : '—'}</div>
        </div>
      </div>

      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Qualgen lot # *</label>
        <input className="input text-sm w-full font-mono" value={qualgenLot}
                onChange={e => setQualgenLot(e.target.value)} />
      </div>

      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Expiration date *</label>
        <input type="date" className="input text-sm w-full" value={expDate}
                onChange={e => setExpDate(e.target.value)} />
      </div>

      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                   value={notes} onChange={e => setNotes(e.target.value)} />
      </div>

      <div className="border border-amber-200 bg-amber-50/40 rounded p-2">
        <label className="text-[10px] uppercase text-amber-800 font-semibold block mb-1">
          Reason *
        </label>
        <input className="input text-sm w-full"
                placeholder="e.g. correcting placeholder lot # / typo fix / re-labeled"
                value={reason}
                onChange={e => setReason(e.target.value)} />
        <div className="text-[10px] text-amber-800 mt-0.5">
          Required — recorded in the audit log so the trail explains the change.
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => save.mutate()}
                disabled={!canSave || save.isPending}>
          <Save size={12}/> {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Drawer>
  )
}


function TransferRow({ t }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [witness, setWitness] = useState('')
  const [courier, setCourier] = useState('')
  const [courierNotes, setCourierNotes] = useState('')

  const recv = useMutation({
    mutationFn: () => api.post(`/pellets/transfers/${t.id}/receive`,
                                { witness_user: witness || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      setOpen(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Receive failed'),
  })

  const pickup = useMutation({
    mutationFn: () => api.post(`/pellets/transfers/${t.id}/take-custody`,
                                { courier_user: courier,
                                  courier_notes: courierNotes || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      setOpen(false); setCourier(''); setCourierNotes('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Pickup failed'),
  })

  const isPacked = t.status === 'packed'
  const baseCls = t.is_stale
    ? 'bg-red-50 border border-red-200'
    : (isPacked ? 'bg-amber-50' : 'bg-blue-50')

  return (
    <li className={`${baseCls} px-2 py-1 rounded`}>
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <span className="flex-1 min-w-0">
          <strong>{t.doses}</strong> doses ·{' '}
          {t.dose_label && <span className="font-medium">{t.dose_label}</span>}
          {t.is_controlled && (
            <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
          )}
          {' · '}{LOC_LABEL[t.from_location]} → {LOC_LABEL[t.to_location]}
          <span className={`text-[10px] ml-1 ${t.is_stale ? 'text-red-700 font-semibold' : 'text-gray-500'}`}>
            ({t.hours_in_state}h
            {t.is_stale && ' · STALE'}
            )
          </span>
        </span>
        <button className="text-plum-700 hover:underline" onClick={() => setOpen(!open)}>
          {open ? 'Cancel' : (isPacked ? 'Take custody' : 'Mark received')}
        </button>
      </div>
      <div className="text-[10px] text-gray-500 mt-0.5">
        packed by {(t.sent_by || '').split('@')[0]}
        {t.courier_user && <> · courier {t.courier_user.split('@')[0]}</>}
      </div>
      {open && isPacked && (
        <div className="mt-1 space-y-1">
          <input className="input text-[11px] w-full"
                 placeholder="Courier email (different person if Sch III)"
                 value={courier} onChange={e => setCourier(e.target.value)} />
          <input className="input text-[11px] w-full"
                 placeholder="Courier notes (optional)"
                 value={courierNotes} onChange={e => setCourierNotes(e.target.value)} />
          <button className="btn-primary text-[11px] w-full"
                   onClick={() => pickup.mutate()}
                   disabled={pickup.isPending || !courier.trim()}>
            <CheckCircle2 size={10}/> {pickup.isPending ? 'Signing…' : 'Confirm courier custody'}
          </button>
        </div>
      )}
      {open && !isPacked && (
        <div className="mt-1 flex gap-1 items-center">
          <input className="input text-[11px] flex-1"
                 placeholder="Witness email (Sch III only)"
                 value={witness} onChange={e => setWitness(e.target.value)} />
          <button className="btn-primary text-[11px]"
                   onClick={() => recv.mutate()}
                   disabled={recv.isPending}>
            <CheckCircle2 size={10}/> Confirm
          </button>
        </div>
      )}
    </li>
  )
}


// ─── Ordered Pellets ───────────────────────────────────────────────

const ORDER_STATUS_TONE = {
  placed:             'bg-blue-100 text-blue-800',
  partially_received: 'bg-amber-100 text-amber-800',
  received:           'bg-green-100 text-green-800',
  cancelled:          'bg-gray-100 text-gray-600',
}

function OrderStatusBadge({ status }) {
  const cls = ORDER_STATUS_TONE[status] || 'bg-gray-100 text-gray-700'
  const label = (status || '').replace(/_/g, ' ')
  return <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${cls}`}>{label}</span>
}


function OrdersCard({ orders, lateOrders, inTransitOrders, onPlace,
                       onOpenDetail, onReceiveAgainst }) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const lateIds = new Set((lateOrders || []).map(o => o.id))

  return (
    <div className="card mb-4">
      <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
        <div className="flex items-center gap-1.5">
          <ShoppingCart size={14} className="text-plum-700" />
          <h2 className="text-sm font-semibold text-gray-800">Ordered Pellets</h2>
          <span className="text-[11px] text-muted">(last 5)</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {lateOrders.length > 0 && (
            <span className="text-[11px] flex items-center gap-1 bg-red-50 text-red-700 px-1.5 py-0.5 rounded">
              <AlertTriangle size={11}/> {lateOrders.length} late
            </span>
          )}
          {inTransitOrders.length > 0 && (
            <span className="text-[11px] flex items-center gap-1 bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
              <Truck size={11}/> {inTransitOrders.length} in transit
            </span>
          )}
          <button className="btn-secondary text-[11px] flex items-center gap-1"
                  onClick={onPlace}>
            <Plus size={11}/> Place order
          </button>
        </div>
      </div>

      {orders.length === 0 ? (
        <div className="text-[12px] text-gray-400 italic">
          No orders yet. A receipt cannot be created without an order — start by placing one.
        </div>
      ) : (
        <ul className="text-[12px] divide-y divide-gray-100">
          {orders.map(o => {
            const isLate = lateIds.has(o.id)
            return (
              <li key={o.id} className="py-1.5 flex items-baseline justify-between gap-2 flex-wrap">
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <button className="font-semibold text-plum-700 hover:underline"
                            onClick={() => onOpenDetail(o.id)}>
                      {o.qualgen_order_number || 'order'}
                    </button>
                    <OrderStatusBadge status={o.status} />
                    {o.is_replacement && (
                      <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">
                        replacement
                      </span>
                    )}
                    {isLate && (
                      <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-red-100 text-red-700">
                        late
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-gray-500 mt-0.5">
                    {fmt.date(o.order_date)} · placed by {(o.placed_by || '').split('@')[0]}
                    {o.status !== 'received' && o.expected_delivery_date && (
                      <> · ETA {fmt.date(o.expected_delivery_date)}</>
                    )}
                    {o.attachments?.length > 0 && (
                      <> · <Paperclip size={10} className="inline"/> {o.attachments.length}</>
                    )}
                  </div>
                </div>
                <div className="text-right text-[11px] shrink-0">
                  <div className="font-mono">${o.grand_total?.toFixed(2)}</div>
                  <div className="text-gray-500">{o.doses_total} doses</div>
                </div>
                {(o.status === 'placed' || o.status === 'partially_received') && (
                  <button className="btn-secondary text-[11px] py-0.5"
                          onClick={() => onReceiveAgainst(o.id)}>
                    Receive →
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}


// ─── Place Order drawer ────────────────────────────────────────────

const EMPTY_LINE = { dose_type_id: '', pack_size: 6, pack_count: 1, unit_cost: 0, notes: '' }


function PlaceOrderDrawer({ types, onClose }) {
  const qc = useQueryClient()
  const today = new Date().toISOString().slice(0, 10)

  const [orderNum, setOrderNum] = useState('')
  const [orderDate, setOrderDate] = useState(today)
  const [expected, setExpected] = useState(addBusinessDays(today, 4))
  const [paymentMethod, setPaymentMethod] = useState('credit_card')
  const [paymentConfirmation, setPaymentConfirmation] = useState('')
  const [shippingCost, setShippingCost] = useState('0')
  const [tax, setTax] = useState('0')
  const [notes, setNotes] = useState('')
  const [isReplacement, setIsReplacement] = useState(false)
  const [replacesDisposalId, setReplacesDisposalId] = useState('')
  const [lines, setLines] = useState([{ ...EMPTY_LINE }])
  const [createdId, setCreatedId] = useState(null)
  const [pdf, setPdf] = useState(null)

  const prefillQuery = useMutation({
    mutationFn: () => api.get('/pellets/orders/reorder-prefill').then(r => r.data),
    onSuccess: (data) => {
      if (!data.lines.length) {
        alert('No doses are currently below threshold — nothing to prefill.')
        return
      }
      setLines(data.lines.map(l => ({
        dose_type_id: l.dose_type_id,
        pack_size:    l.pack_size,
        pack_count:   l.pack_count,
        unit_cost:    l.unit_cost,
        notes:        '',
      })))
    },
    onError: () => alert('Could not load reorder suggestions'),
  })

  const disposalsQuery = useQuery({
    queryKey: ['pellet-disposals-replaceable'],
    queryFn: () => api.get('/pellets/disposals?per_page=30').then(r => r.data),
    enabled: isReplacement,
  })
  const disposals = disposalsQuery.data?.disposals || []

  function updLine(i, patch) {
    setLines(prev => prev.map((l, idx) => idx === i ? { ...l, ...patch } : l))
  }
  function addLine() { setLines(prev => [...prev, { ...EMPTY_LINE }]) }
  function removeLine(i) { setLines(prev => prev.filter((_, idx) => idx !== i)) }

  const linesSubtotal = lines.reduce((s, l) =>
    s + (Number(l.unit_cost) || 0) * (Number(l.pack_count) || 0), 0)
  const grandTotal = linesSubtotal + (Number(shippingCost) || 0) + (Number(tax) || 0)
  const dosesTotal = lines.reduce((s, l) =>
    s + (Number(l.pack_size) || 0) * (Number(l.pack_count) || 0), 0)

  const canSave = lines.length > 0 && lines.every(l =>
    l.dose_type_id && l.pack_size > 0 && l.pack_count > 0
  ) && orderDate && (!isReplacement || replacesDisposalId)

  const create = useMutation({
    mutationFn: () => api.post('/pellets/orders', {
      qualgen_order_number:   orderNum.trim() || null,
      order_date:             orderDate,
      expected_delivery_date: expected || null,
      payment_method:         paymentMethod || null,
      payment_confirmation:   paymentConfirmation.trim() || null,
      shipping_cost:          Number(shippingCost) || 0,
      tax:                    Number(tax) || 0,
      is_replacement:         isReplacement,
      replaces_disposal_id:   isReplacement ? replacesDisposalId : null,
      notes:                  notes.trim() || null,
      lines: lines.map(l => ({
        dose_type_id: l.dose_type_id,
        pack_size:    Number(l.pack_size),
        pack_count:   Number(l.pack_count),
        unit_cost:    Number(l.unit_cost) || 0,
        notes:        l.notes || null,
      })),
    }).then(r => r.data),
    onSuccess: async (order) => {
      // If a PDF was attached, upload it as a second step
      if (pdf) {
        try {
          const fd = new FormData()
          fd.append('file', pdf)
          await api.post(`/pellets/orders/${order.id}/attachments`, fd,
                          { headers: { 'Content-Type': 'multipart/form-data' } })
        } catch (e) {
          alert('Order saved, but PDF upload failed: ' +
                  (e?.response?.data?.detail || e.message))
        }
      }
      qc.invalidateQueries({ queryKey: ['pellet-orders-recent'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      setCreatedId(order.id)
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Order create failed'),
  })

  return (
    <Drawer title="Place Qualgen order" onClose={onClose}>
      <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
        Record the order placed on Qualgen's site. Receipts can only be created
        against an open order (unless it's a damaged-pellet replacement). The
        cost-per-dose is computed from <code>unit cost ÷ pack size</code>.
      </div>

      <div className="flex gap-2 flex-wrap">
        <button className="btn-secondary text-[11px] flex items-center gap-1"
                onClick={() => prefillQuery.mutate()}
                disabled={prefillQuery.isPending}>
          <Wrench size={11}/> Prefill from reorder alerts
        </button>
        <label className="text-[11px] flex items-center gap-1 ml-auto">
          <input type="checkbox" checked={isReplacement}
                 onChange={e => setIsReplacement(e.target.checked)} />
          <span>Damaged-pellet replacement (manufacturer resend)</span>
        </label>
      </div>

      {isReplacement && (
        <div className="border border-violet-200 bg-violet-50/40 rounded p-2 space-y-1">
          <label className="text-[10px] uppercase text-violet-800 block">
            Replacing disposal *
          </label>
          <select className="input text-[12px] w-full"
                   value={replacesDisposalId}
                   onChange={e => setReplacesDisposalId(e.target.value)}>
            <option value="">— pick a disposal that is being resent —</option>
            {disposals.map(d => (
              <option key={d.id} value={d.id}>
                {fmt.date(d.occurred_at?.slice(0, 10))} · {d.doses}× {d.lot_label || ''}
                {' '}· {d.reason}{d.qualgen_lot ? ` · lot ${d.qualgen_lot}` : ''}
              </option>
            ))}
          </select>
          {disposalsQuery.isLoading && (
            <div className="text-[11px] text-gray-400 italic">Loading disposals…</div>
          )}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Qualgen order #</label>
          <input className="input text-sm w-full" value={orderNum}
                  onChange={e => setOrderNum(e.target.value)} />
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Order date *</label>
          <input type="date" className="input text-sm w-full" value={orderDate}
                  onChange={e => {
                    setOrderDate(e.target.value)
                    setExpected(addBusinessDays(e.target.value, 4))
                  }} />
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Expected delivery</label>
          <input type="date" className="input text-sm w-full" value={expected}
                  onChange={e => setExpected(e.target.value)} />
          <div className="text-[10px] text-gray-400 mt-0.5">default = order + 4 business days</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Payment method</label>
          <select className="input text-sm w-full" value={paymentMethod}
                   onChange={e => setPaymentMethod(e.target.value)}>
            <option value="">—</option>
            <option value="credit_card">Credit card</option>
            <option value="ach">ACH</option>
            <option value="check">Check</option>
            <option value="wire">Wire</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Confirmation #</label>
          <input className="input text-sm w-full font-mono"
                  value={paymentConfirmation}
                  onChange={e => setPaymentConfirmation(e.target.value)} />
        </div>
        <div>
          <label className="text-[10px] uppercase text-gray-500 block mb-1">Shipping cost</label>
          <input type="number" step="0.01" className="input text-sm w-full font-mono"
                  value={shippingCost} onChange={e => setShippingCost(e.target.value)} />
        </div>
      </div>

      {/* Lines */}
      <div className="border border-border-subtle rounded">
        <div className="px-2 py-1.5 bg-gray-50 border-b border-border-subtle text-[11px] font-semibold flex items-center justify-between">
          <span>Line items ({lines.length})</span>
          <button className="text-plum-700 hover:underline" onClick={addLine}>
            + Add line
          </button>
        </div>
        <div className="divide-y divide-gray-100">
          {lines.map((l, i) => {
            const dose = types.find(t => t.id === l.dose_type_id)
            const lineDoses = (Number(l.pack_size) || 0) * (Number(l.pack_count) || 0)
            const lineTotal = (Number(l.unit_cost) || 0) * (Number(l.pack_count) || 0)
            const costPerDose = l.pack_size > 0 ? (Number(l.unit_cost) || 0) / Number(l.pack_size) : 0
            return (
              <div key={i} className="p-2 space-y-1">
                <div className="flex items-baseline justify-between text-[11px]">
                  <span className="text-gray-500">Line {i + 1}</span>
                  {lines.length > 1 && (
                    <button className="text-red-600 hover:underline"
                             onClick={() => removeLine(i)}>Remove</button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] uppercase text-gray-500 block mb-1">Dose *</label>
                    <select className="input text-[12px] w-full"
                             value={l.dose_type_id}
                             onChange={e => updLine(i, { dose_type_id: e.target.value })}>
                      <option value="">— choose —</option>
                      {types.map(t => (
                        <option key={t.id} value={t.id}>
                          {t.label}{t.is_controlled ? ' (Sch III)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="grid grid-cols-3 gap-1">
                    <div>
                      <label className="text-[10px] uppercase text-gray-500 block mb-1">Pack</label>
                      <select className="input text-[12px] w-full"
                               value={l.pack_size}
                               onChange={e => updLine(i, { pack_size: Number(e.target.value) })}>
                        {(dose?.pack_sizes || [6, 12, 30]).map(p => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-[10px] uppercase text-gray-500 block mb-1">#packs</label>
                      <input type="number" min="1" className="input text-[12px] w-full"
                              value={l.pack_count}
                              onChange={e => updLine(i, { pack_count: Number(e.target.value) || 0 })} />
                    </div>
                    <div>
                      <label className="text-[10px] uppercase text-gray-500 block mb-1">Doses</label>
                      <div className="input text-[12px] w-full font-mono bg-gray-50 text-gray-600">
                        {lineDoses}
                      </div>
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-[10px] uppercase text-gray-500 block mb-1">Unit cost (per pack)</label>
                    <input type="number" step="0.01" min="0"
                            className="input text-[12px] w-full font-mono"
                            value={l.unit_cost}
                            onChange={e => updLine(i, { unit_cost: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-[10px] uppercase text-gray-500 block mb-1">Cost / dose</label>
                    <div className="input text-[12px] w-full font-mono bg-gray-50 text-gray-600">
                      ${costPerDose.toFixed(2)}
                    </div>
                  </div>
                  <div>
                    <label className="text-[10px] uppercase text-gray-500 block mb-1">Line total</label>
                    <div className="input text-[12px] w-full font-mono bg-gray-50 text-gray-600">
                      ${lineTotal.toFixed(2)}
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-4 gap-2 text-[11px]">
        <div>
          <div className="text-[10px] uppercase text-gray-500">Lines subtotal</div>
          <div className="font-mono">${linesSubtotal.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Shipping</div>
          <div className="font-mono">${(Number(shippingCost) || 0).toFixed(2)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Tax</div>
          <input type="number" step="0.01" className="input text-[12px] w-full font-mono"
                  value={tax} onChange={e => setTax(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Grand total</div>
          <div className="font-mono font-bold">${grandTotal.toFixed(2)}</div>
        </div>
      </div>

      <div>
        <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
        <textarea className="input text-[12px] w-full" rows={2}
                   value={notes} onChange={e => setNotes(e.target.value)} />
      </div>

      <div className="border border-border-subtle rounded p-2">
        <label className="text-[10px] uppercase text-gray-500 flex items-center gap-1 mb-1">
          <Paperclip size={11}/> Invoice / receipt PDF (optional)
        </label>
        <input type="file" accept="application/pdf"
                onChange={e => setPdf(e.target.files?.[0] || null)} />
        {pdf && (
          <div className="text-[11px] text-gray-600 mt-1">
            {pdf.name} · {Math.round(pdf.size / 1024)}KB
          </div>
        )}
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => create.mutate()}
                disabled={!canSave || create.isPending}>
          <Save size={12}/>
          {create.isPending ? 'Saving…' : 'Place order'}
        </button>
      </div>
      <div className="text-[10px] text-gray-400 text-right">
        Total: <strong>{dosesTotal}</strong> doses across <strong>{lines.length}</strong> line(s)
      </div>
    </Drawer>
  )
}


// ─── Order Detail drawer (view + PDF attach) ──────────────────────

function OrderDetailDrawer({ orderId, types, onClose, onReceive }) {
  const qc = useQueryClient()
  const { data: o, isLoading } = useQuery({
    queryKey: ['pellet-order', orderId],
    queryFn: () => api.get(`/pellets/orders/${orderId}`).then(r => r.data),
  })
  const [pdf, setPdf] = useState(null)

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', pdf)
      return api.post(`/pellets/orders/${orderId}/attachments`, fd,
                       { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
    },
    onSuccess: () => {
      setPdf(null)
      qc.invalidateQueries({ queryKey: ['pellet-order', orderId] })
      qc.invalidateQueries({ queryKey: ['pellet-orders-recent'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Upload failed'),
  })

  const delAttachment = useMutation({
    mutationFn: (attId) => api.delete(`/pellets/orders/${orderId}/attachments/${attId}`)
                                .then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-order', orderId] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  const cancel = useMutation({
    mutationFn: () => api.post(`/pellets/orders/${orderId}/cancel`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-orders-recent'] })
      qc.invalidateQueries({ queryKey: ['pellet-order', orderId] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Cancel failed'),
  })

  if (isLoading || !o) {
    return (
      <Drawer title="Order detail" onClose={onClose}>
        <div className="text-gray-400 italic">Loading…</div>
      </Drawer>
    )
  }

  const canReceive = o.status === 'placed' || o.status === 'partially_received'

  return (
    <Drawer title={`Order ${o.qualgen_order_number || '(no #)'}`} onClose={onClose}>
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <OrderStatusBadge status={o.status} />
          {o.is_replacement && (
            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">
              replacement
            </span>
          )}
          {o.is_overdue && (
            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-red-100 text-red-700">
              late
            </span>
          )}
        </div>
        {canReceive && (
          <button className="btn-primary text-[11px] flex items-center gap-1"
                  onClick={() => onReceive(orderId)}>
            <Plus size={11}/> Receive shipment
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 text-[12px]">
        <div>
          <div className="text-[10px] uppercase text-gray-500">Order date</div>
          <div>{fmt.date(o.order_date)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Expected delivery</div>
          <div>{o.expected_delivery_date ? fmt.date(o.expected_delivery_date) : '—'}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Placed by</div>
          <div>{o.placed_by}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Payment</div>
          <div>
            {(o.payment_method || '—').replace(/_/g, ' ')}
            {o.payment_confirmation && <span className="text-gray-500"> · {o.payment_confirmation}</span>}
          </div>
        </div>
      </div>

      <div className="border border-border-subtle rounded overflow-hidden">
        <table className="w-full text-[12px]">
          <thead className="bg-gray-50 text-[10px] uppercase text-gray-500">
            <tr>
              <th className="px-2 py-1 text-left">Dose</th>
              <th className="px-2 py-1 text-right">Packs</th>
              <th className="px-2 py-1 text-right">Doses</th>
              <th className="px-2 py-1 text-right">Unit $</th>
              <th className="px-2 py-1 text-right">$/dose</th>
              <th className="px-2 py-1 text-right">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {o.lines.map(l => (
              <tr key={l.id}>
                <td className="px-2 py-1">
                  {l.dose_label}
                  {l.is_controlled && (
                    <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
                  )}
                </td>
                <td className="px-2 py-1 text-right">{l.pack_count}× {l.pack_size}</td>
                <td className="px-2 py-1 text-right font-mono">
                  {l.doses_received}/{l.doses_ordered}
                </td>
                <td className="px-2 py-1 text-right font-mono">${l.unit_cost.toFixed(2)}</td>
                <td className="px-2 py-1 text-right font-mono">
                  ${l.cost_per_dose != null ? l.cost_per_dose.toFixed(2) : '—'}
                </td>
                <td className="px-2 py-1 text-right font-mono">${l.line_total.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot className="bg-gray-50 text-[11px]">
            <tr>
              <td className="px-2 py-1 text-right" colSpan={5}>Lines subtotal</td>
              <td className="px-2 py-1 text-right font-mono">${o.lines_subtotal.toFixed(2)}</td>
            </tr>
            <tr>
              <td className="px-2 py-1 text-right" colSpan={5}>Shipping</td>
              <td className="px-2 py-1 text-right font-mono">${o.shipping_cost.toFixed(2)}</td>
            </tr>
            <tr>
              <td className="px-2 py-1 text-right" colSpan={5}>Tax</td>
              <td className="px-2 py-1 text-right font-mono">${o.tax.toFixed(2)}</td>
            </tr>
            <tr className="font-bold">
              <td className="px-2 py-1 text-right" colSpan={5}>Grand total</td>
              <td className="px-2 py-1 text-right font-mono">${o.grand_total.toFixed(2)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {o.notes && (
        <div className="text-[11px] bg-gray-50 border border-gray-200 rounded p-2">
          <div className="text-[10px] uppercase text-gray-500 mb-0.5">Notes</div>
          {o.notes}
        </div>
      )}

      {o.receipts?.length > 0 && (
        <div className="border border-border-subtle rounded p-2">
          <div className="text-[11px] font-semibold flex items-center gap-1 mb-2">
            <Truck size={11}/> Receipts ({o.receipts.length})
          </div>
          <ul className="text-[12px] space-y-1">
            {o.receipts.map(r => (
              <li key={r.id} className="bg-gray-50 px-2 py-1 rounded">
                <div className="flex items-center justify-between">
                  <span>
                    {fmt.date(r.received_date)}
                    {' · '}
                    {r.manifest_verified
                      ? <span className="text-green-700">verified</span>
                      : <span className="text-amber-700">pending verify</span>}
                  </span>
                  {r.attachments?.length > 0 && (
                    <span className="text-[10px] text-gray-500">
                      <Paperclip size={10} className="inline"/> {r.attachments.length}
                    </span>
                  )}
                </div>
                {r.attachments?.length > 0 && (
                  <ul className="mt-1 ml-3 text-[11px]">
                    {r.attachments.map(a => (
                      <li key={a.id}>
                        <a className="text-plum-700 hover:underline"
                            href={`/api/pellets/receipts/${r.id}/attachments/${a.id}`}
                            target="_blank" rel="noreferrer">
                          {a.filename}
                        </a>
                        <span className="text-gray-400 ml-1">
                          ({Math.round((a.size_bytes || 0) / 1024)}KB)
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Attachments */}
      <div className="border border-border-subtle rounded p-2">
        <div className="text-[11px] font-semibold flex items-center gap-1 mb-2">
          <Paperclip size={11}/> Invoice / receipt PDFs
        </div>
        {o.attachments?.length > 0 ? (
          <ul className="text-[12px] space-y-1 mb-2">
            {o.attachments.map(a => (
              <li key={a.id} className="flex items-center justify-between bg-gray-50 px-2 py-1 rounded">
                <a className="text-plum-700 hover:underline truncate"
                    href={`/api/pellets/orders/${orderId}/attachments/${a.id}`}
                    target="_blank" rel="noreferrer">
                  {a.filename}
                </a>
                <div className="flex items-center gap-2 text-[10px] text-gray-500">
                  <span>{Math.round((a.size_bytes || 0) / 1024)}KB</span>
                  <button className="text-red-600 hover:underline"
                          onClick={() => {
                            if (confirm(`Delete ${a.filename}?`)) {
                              delAttachment.mutate(a.id)
                            }
                          }}>
                    <Trash2 size={10}/>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-[11px] text-gray-400 italic mb-2">No attachments yet.</div>
        )}
        <div className="flex items-center gap-2">
          <input type="file" accept="application/pdf"
                  onChange={e => setPdf(e.target.files?.[0] || null)} />
          <button className="btn-secondary text-[11px] flex items-center gap-1"
                  onClick={() => upload.mutate()}
                  disabled={!pdf || upload.isPending}>
            <Upload size={11}/>
            {upload.isPending ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        {o.status === 'placed' && o.receipts.length === 0 && (
          <button className="text-[11px] text-red-700 hover:underline"
                  onClick={() => {
                    if (confirm('Cancel this order?')) cancel.mutate()
                  }}
                  disabled={cancel.isPending}>
            Cancel order
          </button>
        )}
        <button className="text-sm text-muted hover:underline ml-auto" onClick={onClose}>Close</button>
      </div>
    </Drawer>
  )
}


// Tiny date helper — adds N business days to a YYYY-MM-DD string.
function addBusinessDays(iso, n) {
  if (!iso) return ''
  const d = new Date(iso + 'T00:00:00')
  let added = 0
  while (added < n) {
    d.setDate(d.getDate() + 1)
    const dow = d.getDay()
    if (dow !== 0 && dow !== 6) added += 1
  }
  return d.toISOString().slice(0, 10)
}


// ─── Generic drawer wrapper (used by Place / Detail) ────────────────

function Drawer({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[16px]">{title}</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          {children}
        </div>
      </div>
    </div>
  )
}


// ─── Receive drawer ────────────────────────────────────────────────

function ReceiveDrawer({ types, prefillOrderId, onClose }) {
  const qc = useQueryClient()
  // Gating: must pick an order OR mark this as a replacement
  const [mode, setMode] = useState(prefillOrderId ? 'order' : 'order')   // 'order' | 'replacement'
  const [selectedOrderId, setSelectedOrderId] = useState(prefillOrderId || '')
  const [replacesDisposalId, setReplacesDisposalId] = useState('')

  const [orderNum, setOrderNum] = useState('')
  const [orderedDate, setOrderedDate] = useState('')
  const [receivedDate, setReceivedDate] = useState(new Date().toISOString().slice(0, 10))
  const [notes, setNotes] = useState('')
  const [lots, setLots] = useState([{
    dose_type_id: '', qualgen_lot_number: '', expiration_date: '',
    pack_size: 6, packs_received: 1, doses_received: 6, notes: '',
  }])
  const [witness, setWitness] = useState('')
  const [packingSlip, setPackingSlip] = useState(null)
  const [packingSlipError, setPackingSlipError] = useState('')

  const openOrdersQuery = useQuery({
    queryKey: ['pellet-open-orders'],
    queryFn: () => api.get('/pellets/orders/open').then(r => r.data),
  })
  const openOrders = openOrdersQuery.data?.orders || []

  const disposalsQuery = useQuery({
    queryKey: ['pellet-disposals-recent'],
    queryFn: () => api.get('/pellets/disposals?per_page=30').then(r => r.data),
    enabled: mode === 'replacement',
  })
  const disposals = disposalsQuery.data?.disposals || []

  // When user picks an order, pre-fill the lot rows with that order's lines
  // (one row per line — they still need lot # + expiration from the manifest).
  useEffect(() => {
    if (mode !== 'order' || !selectedOrderId) return
    const o = openOrders.find(x => x.id === selectedOrderId)
    if (!o) return
    setOrderNum(o.qualgen_order_number || '')
    setOrderedDate(o.order_date || '')
    if (o.lines?.length > 0) {
      setLots(o.lines.map(l => ({
        dose_type_id:      l.dose_type_id,
        qualgen_lot_number: '',
        expiration_date:   '',
        pack_size:         l.pack_size,
        packs_received:    l.pack_count,
        doses_received:    l.pack_size * l.pack_count,
        notes:             '',
      })))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrderId, mode, openOrders.length])

  const create = useMutation({
    mutationFn: () => api.post('/pellets/receipts', {
      qualgen_order_number: orderNum || null,
      ordered_date: orderedDate || null,
      received_date: receivedDate,
      location: 'white_plains',
      lots,
      notes: notes || null,
      order_id: mode === 'order' ? selectedOrderId : null,
      is_replacement: mode === 'replacement',
      replaces_disposal_id: mode === 'replacement' ? replacesDisposalId : null,
    }).then(r => r.data),
    onError: (e) => alert(e?.response?.data?.detail || 'Receive failed'),
  })

  const verify = useMutation({
    mutationFn: (receiptId) => api.post(`/pellets/receipts/${receiptId}/verify-manifest`,
                                          { witness_user: witness || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      qc.invalidateQueries({ queryKey: ['pellet-dose-types'] })
      qc.invalidateQueries({ queryKey: ['pellet-orders-recent'] })
      qc.invalidateQueries({ queryKey: ['pellet-open-orders'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Manifest verify failed'),
  })

  function updLot(i, patch) {
    setLots(prev => prev.map((l, idx) => idx === i ? { ...l, ...patch } : l))
  }

  function addLot() {
    setLots(prev => [...prev, {
      dose_type_id: '', qualgen_lot_number: '', expiration_date: '',
      pack_size: 6, packs_received: 1, doses_received: 6, notes: '',
    }])
  }
  function removeLot(i) {
    setLots(prev => prev.filter((_, idx) => idx !== i))
  }

  const hasControlled = lots.some(l => {
    const t = types.find(x => x.id === l.dose_type_id)
    return t?.is_controlled
  })

  const gatingOk = (mode === 'order' && selectedOrderId) ||
                     (mode === 'replacement' && replacesDisposalId)

  const canCreate = gatingOk && lots.length > 0 && lots.every(l =>
    l.dose_type_id && l.qualgen_lot_number && l.expiration_date &&
    l.doses_received > 0
  )

  async function submit() {
    setPackingSlipError('')
    const r = await create.mutateAsync()
    if (packingSlip) {
      try {
        const fd = new FormData()
        fd.append('file', packingSlip)
        await api.post(`/pellets/receipts/${r.receipt_id}/attachments`, fd,
                        { headers: { 'Content-Type': 'multipart/form-data' } })
      } catch (e) {
        // Don't block verification — surface the error but continue. The
        // receipt + lots already exist; the packing slip can be re-uploaded
        // from the order detail drawer.
        setPackingSlipError(e?.response?.data?.detail || 'Packing slip upload failed')
      }
    }
    await verify.mutateAsync(r.receipt_id)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Receive Qualgen shipment</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
            A receipt must match an open Qualgen order — or be marked as a
            damaged-pellet replacement. Pick one below, then enter every lot
            from the shipping manifest. Saving creates the receipt; manifest
            verification pushes doses into White Plains stock.
          </div>

          {/* Mode selector: order vs replacement */}
          <div className="border border-border-subtle rounded p-2 space-y-2">
            <div className="flex items-center gap-3 text-[12px]">
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="radio" checked={mode === 'order'}
                        onChange={() => setMode('order')} />
                <span>Against an open order</span>
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="radio" checked={mode === 'replacement'}
                        onChange={() => setMode('replacement')} />
                <span>Damaged-pellet replacement</span>
              </label>
            </div>
            {mode === 'order' ? (
              <div>
                <label className="text-[10px] uppercase text-gray-500 block mb-1">Open order *</label>
                <select className="input text-sm w-full"
                         value={selectedOrderId}
                         onChange={e => setSelectedOrderId(e.target.value)}>
                  <option value="">— pick an open order —</option>
                  {openOrders.map(o => (
                    <option key={o.id} value={o.id}>
                      {o.qualgen_order_number || '(no #)'} · {fmt.date(o.order_date)}
                      {' '}· {o.status.replace(/_/g, ' ')}
                      {' '}· {o.doses_total} doses · ${o.grand_total?.toFixed(2)}
                    </option>
                  ))}
                </select>
                {openOrdersQuery.isLoading && (
                  <div className="text-[11px] text-gray-400 italic mt-1">Loading orders…</div>
                )}
                {!openOrdersQuery.isLoading && openOrders.length === 0 && (
                  <div className="text-[11px] text-red-700 mt-1">
                    No open orders. Place an order first, or switch to "Damaged-pellet replacement".
                  </div>
                )}
              </div>
            ) : (
              <div>
                <label className="text-[10px] uppercase text-gray-500 block mb-1">Replacing disposal *</label>
                <select className="input text-sm w-full"
                         value={replacesDisposalId}
                         onChange={e => setReplacesDisposalId(e.target.value)}>
                  <option value="">— pick the disposal being resent —</option>
                  {disposals.map(d => (
                    <option key={d.id} value={d.id}>
                      {fmt.date(d.occurred_at?.slice(0, 10))} · {d.doses}× {d.lot_label || ''}
                      {' '}· {d.reason}{d.qualgen_lot ? ` · lot ${d.qualgen_lot}` : ''}
                    </option>
                  ))}
                </select>
                <div className="text-[11px] text-gray-500 mt-1">
                  Replacement receipts skip the order — Qualgen is resending pellets
                  for a lot that was disposed (broken/dropped/etc.).
                </div>
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Qualgen order #</label>
              <input className="input text-sm w-full" value={orderNum}
                     onChange={e => setOrderNum(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Ordered date</label>
              <input type="date" className="input text-sm w-full"
                     value={orderedDate}
                     onChange={e => setOrderedDate(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Received date *</label>
              <input type="date" className="input text-sm w-full"
                     value={receivedDate}
                     onChange={e => setReceivedDate(e.target.value)} />
            </div>
          </div>

          <div className="border border-border-subtle rounded">
            <div className="px-2 py-1.5 bg-gray-50 border-b border-border-subtle text-[11px] font-semibold flex items-center justify-between">
              <span>Lots received ({lots.length})</span>
              <button className="text-plum-700 hover:underline" onClick={addLot}>
                + Add lot
              </button>
            </div>
            <div className="divide-y divide-gray-100">
              {lots.map((l, i) => (
                <div key={i} className="p-2 space-y-2">
                  <div className="flex items-baseline justify-between">
                    <div className="text-[11px] text-gray-500">Lot {i + 1}</div>
                    {lots.length > 1 && (
                      <button className="text-red-600 hover:underline text-[11px]"
                               onClick={() => removeLot(i)}>Remove</button>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] uppercase text-gray-500 block mb-1">Dose *</label>
                      <select className="input text-[12px] w-full"
                               value={l.dose_type_id}
                               onChange={e => updLot(i, { dose_type_id: e.target.value })}>
                        <option value="">— choose dose —</option>
                        {types.map(t => (
                          <option key={t.id} value={t.id}>
                            {t.label}{t.is_controlled ? ' (Sch III)' : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-[10px] uppercase text-gray-500 block mb-1">Qualgen lot # *</label>
                      <input className="input text-[12px] w-full font-mono"
                              value={l.qualgen_lot_number}
                              onChange={e => updLot(i, { qualgen_lot_number: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] uppercase text-gray-500 block mb-1">Expiration *</label>
                      <input type="date" className="input text-[12px] w-full"
                              value={l.expiration_date}
                              onChange={e => updLot(i, { expiration_date: e.target.value })} />
                    </div>
                    <div className="grid grid-cols-3 gap-1">
                      <div>
                        <label className="text-[10px] uppercase text-gray-500 block mb-1">Pack</label>
                        <select className="input text-[12px] w-full"
                                 value={l.pack_size}
                                 onChange={e => {
                                   const p = Number(e.target.value)
                                   updLot(i, { pack_size: p,
                                                doses_received: p * (l.packs_received || 1) })
                                 }}>
                          {[6, 12, 30].map(p => <option key={p} value={p}>{p}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] uppercase text-gray-500 block mb-1">#packs</label>
                        <input type="number" min="1" className="input text-[12px] w-full"
                                value={l.packs_received}
                                onChange={e => {
                                  const n = Number(e.target.value) || 1
                                  updLot(i, { packs_received: n,
                                               doses_received: (l.pack_size || 6) * n })
                                }} />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase text-gray-500 block mb-1">Doses</label>
                        <input type="number" className="input text-[12px] w-full font-mono"
                                value={l.doses_received}
                                onChange={e => updLot(i, { doses_received: Number(e.target.value) || 0 })} />
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-[12px] w-full" rows={2}
                       value={notes} onChange={e => setNotes(e.target.value)} />
          </div>

          <div className="border border-border-subtle rounded p-2">
            <label className="text-[10px] uppercase text-gray-500 flex items-center gap-1 mb-1">
              <Paperclip size={11}/> Packing slip PDF (optional)
            </label>
            <input type="file" accept="application/pdf"
                    onChange={e => setPackingSlip(e.target.files?.[0] || null)} />
            {packingSlip && (
              <div className="text-[11px] text-gray-600 mt-1">
                {packingSlip.name} · {Math.round(packingSlip.size / 1024)}KB
              </div>
            )}
            {packingSlipError && (
              <div className="text-[11px] text-red-700 mt-1">{packingSlipError}</div>
            )}
            <div className="text-[10px] text-gray-400 mt-0.5">
              Attached to the receipt audit trail. Can also be added later from the order detail.
            </div>
          </div>

          {hasControlled && (
            <div className="border border-amber-200 bg-amber-50/50 rounded p-2">
              <div className="text-[11px] text-amber-800 font-semibold flex items-center gap-1 mb-1">
                <Shield size={11} /> Schedule III witness required
              </div>
              <input className="input text-[12px] w-full"
                      placeholder="Witness email (must be a different person)"
                      value={witness}
                      onChange={e => setWitness(e.target.value)} />
            </div>
          )}
        </div>

        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={submit}
                  disabled={!canCreate || create.isPending || verify.isPending ||
                             (hasControlled && !witness.trim())}>
            <Save size={12}/>
            {create.isPending || verify.isPending
              ? 'Saving…'
              : 'Receive + verify manifest'}
          </button>
        </div>
      </div>
    </div>
  )
}


// ─── Transfer drawer ───────────────────────────────────────────────

function TransferDrawer({ onClose, initial }) {
  const qc = useQueryClient()
  const [lotId, setLotId] = useState(initial?.lot_id || '')
  const [fromLoc, setFromLoc] = useState(initial?.from_location || 'white_plains')
  const [toLoc, setToLoc] = useState(() => {
    // Default destination ≠ from
    const from = initial?.from_location || 'white_plains'
    return from === 'brandywine' ? 'white_plains' : 'brandywine'
  })
  const [doses, setDoses] = useState(initial?.max_doses ? Math.min(1, initial.max_doses) : 1)
  const [witness, setWitness] = useState('')
  const [notes, setNotes] = useState('')
  const [courierNow, setCourierNow] = useState(false)
  const [courierUser, setCourierUser] = useState('')
  const [courierNotes, setCourierNotes] = useState('')

  const { data } = useQuery({
    queryKey: ['pellet-lots', fromLoc],
    queryFn: () => api.get('/pellets/lots', { params: { location: fromLoc } }).then(r => r.data),
  })
  const lots = data?.lots || []
  const selected = lots.find(l => l.id === lotId)

  const create = useMutation({
    mutationFn: () => api.post('/pellets/transfers', {
      lot_id: lotId, from_location: fromLoc, to_location: toLoc,
      doses: Number(doses), witness_user: witness || null,
      notes: notes || null,
      courier_user:  courierNow ? (courierUser.trim() || null) : null,
      courier_notes: courierNow ? (courierNotes || null) : null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      qc.invalidateQueries({ queryKey: ['pellet-lots'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Transfer failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Transfer Between Locations</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">From</label>
              <select className="input text-sm w-full" value={fromLoc}
                       onChange={e => { setFromLoc(e.target.value); setLotId('') }}>
                {Object.entries(LOC_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">To</label>
              <select className="input text-sm w-full" value={toLoc}
                       onChange={e => setToLoc(e.target.value)}>
                {Object.entries(LOC_LABEL).map(([k, v]) => (
                  <option key={k} value={k} disabled={k === fromLoc}>{v}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Lot</label>
            <select className="input text-sm w-full" value={lotId}
                     onChange={e => setLotId(e.target.value)}>
              <option value="">— choose lot —</option>
              {lots.map(l => (
                <option key={l.id} value={l.id}>
                  {l.dose_type_label} · {l.qualgen_lot_number} · exp {l.expiration_date} · {l.balances?.[fromLoc] || 0} doses
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Doses to send</label>
            <input type="number" min="1" max={selected?.balances?.[fromLoc] || undefined}
                    className="input text-sm w-full font-mono"
                    value={doses} onChange={e => setDoses(e.target.value)} />
          </div>
          {selected?.is_controlled && (
            <div className="border border-amber-200 bg-amber-50/50 rounded p-2">
              <div className="text-[11px] text-amber-800 font-semibold flex items-center gap-1 mb-1">
                <Shield size={11} /> Schedule III witness required
              </div>
              <input className="input text-[12px] w-full"
                      placeholder="Witness email (must be different)"
                      value={witness} onChange={e => setWitness(e.target.value)} />
            </div>
          )}
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-[12px] w-full" rows={2}
                       value={notes} onChange={e => setNotes(e.target.value)} />
          </div>

          <div className="border border-border-subtle rounded p-2 space-y-1">
            <label className="text-[12px] flex items-center gap-2">
              <input type="checkbox" checked={courierNow}
                      onChange={e => setCourierNow(e.target.checked)} />
              <span>Courier is taking custody now</span>
            </label>
            <div className="text-[10px] text-gray-500">
              Leave unchecked when the pellets sit at the source waiting for pickup.
              {selected?.is_controlled && (
                <> Sch III requires a courier signature before destination receive — they can
                also sign in later via the dashboard "Take custody" action.</>
              )}
            </div>
            {courierNow && (
              <>
                <input className="input text-[12px] w-full mt-1"
                        placeholder={selected?.is_controlled
                          ? 'Courier email (must differ from packer for Sch III)'
                          : 'Courier email'}
                        value={courierUser}
                        onChange={e => setCourierUser(e.target.value)} />
                <input className="input text-[12px] w-full"
                        placeholder="Courier notes (optional)"
                        value={courierNotes}
                        onChange={e => setCourierNotes(e.target.value)} />
              </>
            )}
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => create.mutate()}
                  disabled={!lotId || doses <= 0 ||
                             (selected?.is_controlled && !witness.trim()) ||
                             create.isPending}>
            <ArrowRightLeft size={12}/> {create.isPending ? 'Sending…' : 'Send transfer'}
          </button>
        </div>
      </div>
    </div>
  )
}


// ─── Disposal drawer ───────────────────────────────────────────────

function DisposeDrawer({ onClose }) {
  const qc = useQueryClient()
  const [location, setLocation] = useState('white_plains')
  const [lotId, setLotId] = useState('')
  const [doses, setDoses] = useState(1)
  const [reason, setReason] = useState('dropped')
  const [witness, setWitness] = useState('')
  const [notes, setNotes] = useState('')

  const { data: picks } = useQuery({
    queryKey: ['pellet-picklists'],
    queryFn: () => api.get('/pellets/picklists').then(r => r.data),
    staleTime: 300_000,
  })
  const { data } = useQuery({
    queryKey: ['pellet-lots', location],
    queryFn: () => api.get('/pellets/lots', { params: { location } }).then(r => r.data),
  })
  const lots = data?.lots || []
  const selected = lots.find(l => l.id === lotId)

  const create = useMutation({
    mutationFn: () => api.post('/pellets/disposals', {
      lot_id: lotId, location, doses: Number(doses), reason,
      witness_user: witness || null, notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      qc.invalidateQueries({ queryKey: ['pellet-lots'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Disposal failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Dispose pellets</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
            Disposal sends pellets to the biohazard sharps container. We
            do <strong>not</strong> contact Qualgen for refunds — the
            practice absorbs the loss. This action decrements stock
            immediately and writes a permanent audit row.
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
              <select className="input text-sm w-full" value={location}
                       onChange={e => { setLocation(e.target.value); setLotId('') }}>
                {Object.entries(LOC_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Reason</label>
              <select className="input text-sm w-full" value={reason}
                       onChange={e => setReason(e.target.value)}>
                {(picks?.disposal_reasons || []).map(r => (
                  <option key={r.v} value={r.v}>{r.l}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Lot</label>
            <select className="input text-sm w-full" value={lotId}
                     onChange={e => setLotId(e.target.value)}>
              <option value="">— choose lot —</option>
              {lots.map(l => (
                <option key={l.id} value={l.id}>
                  {l.dose_type_label} · {l.qualgen_lot_number} · exp {l.expiration_date} · {l.balances?.[location] || 0} doses
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Doses to dispose</label>
            <input type="number" min="1" max={selected?.balances?.[location] || undefined}
                    className="input text-sm w-full font-mono"
                    value={doses} onChange={e => setDoses(e.target.value)} />
          </div>
          {selected?.is_controlled && (
            <div className="border border-amber-200 bg-amber-50/50 rounded p-2">
              <div className="text-[11px] text-amber-800 font-semibold flex items-center gap-1 mb-1">
                <Shield size={11} /> Schedule III witness required
              </div>
              <input className="input text-[12px] w-full"
                      placeholder="Witness email (must be different)"
                      value={witness} onChange={e => setWitness(e.target.value)} />
            </div>
          )}
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">
              Notes {reason === 'other' && <span className="text-red-600">*</span>}
            </label>
            <textarea className="input text-[12px] w-full" rows={2}
                       value={notes} onChange={e => setNotes(e.target.value)} />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => create.mutate()}
                  disabled={!lotId || doses <= 0 ||
                             (selected?.is_controlled && !witness.trim()) ||
                             (reason === 'other' && !notes.trim()) ||
                             create.isPending}>
            <Trash2 size={12}/> {create.isPending ? 'Disposing…' : 'Confirm disposal'}
          </button>
        </div>
      </div>
    </div>
  )
}
