import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Search, Plus } from 'lucide-react'
import api, { fmt } from '../utils/api'

export default function Patients() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ['patients', search, page],
    queryFn: () => api.get('/patients', { params: { search, page, per_page: 50 } }).then(r => r.data),
  })

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="page-title">Patients</h1>
          <p className="text-gray-500 text-sm mt-1">{data?.total?.toLocaleString() || 0} patients</p>
        </div>
      </div>

      <div className="card mb-4">
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-9"
            placeholder="Search by name, MRN, insurance ID…"
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
          />
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="table-th">Patient</th>
              <th className="table-th">MRN</th>
              <th className="table-th">DOB</th>
              <th className="table-th">Primary Insurance</th>
              <th className="table-th">Member ID</th>
              <th className="table-th">Secondary</th>
              <th className="table-th">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 py-8">Loading…</td></tr>
            )}
            {!isLoading && data?.patients?.length === 0 && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 py-8">No patients found. Import an ERA file to automatically create patient records.</td></tr>
            )}
            {data?.patients?.map(p => (
              <tr key={p.id} className="table-row cursor-pointer" onClick={() => navigate(`/patients/${p.id}`)}>
                <td className="table-td font-medium">{p.full_name}</td>
                <td className="table-td font-mono text-xs">{p.patient_id}</td>
                <td className="table-td text-xs">{fmt.date(p.date_of_birth)}</td>
                <td className="table-td text-xs max-w-[140px] truncate">{p.primary_insurance_name || '—'}</td>
                <td className="table-td font-mono text-xs">{p.primary_insurance_id || '—'}</td>
                <td className="table-td text-xs text-gray-400">{p.secondary_insurance_name || '—'}</td>
                <td className="table-td">
                  <button
                    className="text-xs text-plum-700 hover:underline"
                    onClick={e => { e.stopPropagation(); navigate(`/patients/${p.id}`) }}
                  >
                    View Ledger
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
