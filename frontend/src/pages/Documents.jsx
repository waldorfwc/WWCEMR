import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BookOpen } from 'lucide-react'
import api from '../utils/api'
import ChartPatientList from '../components/ChartPatientList'
import FaxLogPane from './documents/FaxLogPane'

export default function Documents() {
  const { data: indexStatus } = useQuery({
    queryKey: ['doc-index-status'],
    queryFn: () => api.get('/documents/index/status').then(r => r.data),
  })

  const totalDocs = indexStatus?.indexed_documents || 0
  const totalPatients = indexStatus?.indexed_patients || 0

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] tracking-tight m-0">Patient Charts</h1>
          <div className="text-muted text-[12px] mt-0.5">
            <span className="font-serif text-ink font-semibold text-[14px]">{totalDocs.toLocaleString()}</span> documents
            <span className="mx-1">·</span>
            <span className="font-serif text-ink font-semibold text-[14px]">{totalPatients.toLocaleString()}</span> patients
          </div>
        </div>
        <Link
          to="/documents/manual"
          className="flex items-center gap-1 text-sm text-gray-500 hover:text-plum-700 px-1"
          title="Charts & Documents Manual"
        >
          <BookOpen size={14} /> Manual
        </Link>
      </div>

      {/* Two-pane layout */}
      <div className="grid gap-3" style={{ gridTemplateColumns: '280px 1fr', minHeight: 'calc(100vh - 180px)' }}>
        <ChartPatientList />
        <FaxLogPane />
      </div>
    </div>
  )
}
