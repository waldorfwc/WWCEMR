import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search, FolderOpen } from 'lucide-react'
import api from '../utils/api'

export default function Documents() {
  const navigate = useNavigate()
  const [chartSearch, setChartSearch] = useState('')
  const [page, setPage] = useState(1)
  const PER_PAGE = 100

  const { data: indexStatus } = useQuery({
    queryKey: ['doc-index-status'],
    queryFn: () => api.get('/documents/index/status').then(r => r.data),
  })

  const { data: patients, isLoading } = useQuery({
    queryKey: ['doc-patients', chartSearch, page],
    queryFn: () => api.get('/documents/patients', {
      params: { search: chartSearch || undefined, page, per_page: PER_PAGE },
    }).then(r => r.data),
    enabled: (indexStatus?.indexed_documents || 0) > 0,
  })

  const indexed = indexStatus?.indexed_documents || 0

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Patient Sidebar */}
      <div className="w-60 border-r bg-white flex flex-col shrink-0">
        <div className="p-3 border-b">
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Patients</div>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-2.5 text-gray-400" />
            <input
              className="w-full pl-6 pr-2 py-1.5 border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary-400"
              placeholder="Name or Chart #..."
              value={chartSearch}
              onChange={e => { setChartSearch(e.target.value); setPage(1) }}
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <div className="px-3 py-2 text-xs text-gray-500 border-b bg-gray-50">
            {patients?.total?.toLocaleString() || 0} patients
          </div>
          {isLoading ? (
            <div className="text-center text-gray-400 text-xs py-8">Loading...</div>
          ) : (
            patients?.patients?.map(p => (
              <button
                key={p.chart_number}
                onClick={() => navigate(`/chart/${p.chart_number}`)}
                className="w-full text-left px-3 py-2.5 text-xs border-b hover:bg-blue-50 transition-colors"
              >
                <div className="font-semibold text-gray-800">
                  {p.patient_name || `Chart ${p.chart_number}`}
                </div>
                <div className="flex justify-between text-gray-400 mt-0.5">
                  <span>{p.chart_number}</span>
                  <span>{p.document_count} docs</span>
                </div>
                {p.dob && <div className="text-gray-300 text-[10px]">DOB {p.dob}</div>}
              </button>
            ))
          )}
          {/* Pagination */}
          {patients && patients.total > PER_PAGE && (
            <div className="flex items-center justify-center gap-2 py-3 text-xs text-gray-400">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-2 py-1 border rounded disabled:opacity-40"
              >
                Prev
              </button>
              <span>{page} / {Math.ceil(patients.total / PER_PAGE)}</span>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={page >= Math.ceil(patients.total / PER_PAGE)}
                className="px-2 py-1 border rounded disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main Content — prompt to select a patient */}
      <div className="flex-1 flex items-center justify-center bg-gray-50">
        <div className="text-center text-gray-400">
          <FolderOpen size={48} className="mx-auto mb-3 text-gray-300" />
          <div className="text-sm font-medium">Select a patient to view their chart</div>
          <div className="text-xs mt-1">
            {indexed > 0
              ? `${indexed.toLocaleString()} documents across ${indexStatus?.indexed_patients?.toLocaleString() || ''} patients`
              : 'Loading...'}
          </div>
        </div>
      </div>
    </div>
  )
}
