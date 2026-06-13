import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Settings } from 'lucide-react'

const TABS = [
  { id: 'alerts',    label: 'Alerts & Windows' },
  { id: 'steps',     label: 'Workflow Steps' },
  { id: 'postop',    label: 'Post-Op Schedules' },
  { id: 'capacity',  label: 'Facilities & Capacity' },
  { id: 'templates', label: 'Templates' },
]

export default function SurgerySettings() {
  const [tab, setTab] = useState('alerts')
  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/surgery" className="text-muted hover:text-plum-700">
          <ArrowLeft size={18} />
        </Link>
        <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
          <Settings size={22} className="text-plum-700" />
          Surgery Settings
        </h1>
      </div>
      <div className="flex gap-1 border-b border-border-subtle mb-6">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  className={`px-3 py-2 text-[13px] border-b-2 -mb-px transition ${
                    tab === t.id
                      ? 'border-plum-700 text-plum-700 font-medium'
                      : 'border-transparent text-muted hover:text-plum-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'alerts'    && <AlertsTab />}
      {tab === 'steps'     && <StepsTab />}
      {tab === 'postop'    && <PostOpTab />}
      {tab === 'capacity'  && <CapacityTab />}
      {tab === 'templates' && <TemplatesTab />}
    </div>
  )
}

function Placeholder({ name }) {
  return <div className="text-muted text-sm">{name} — coming in this release.</div>
}
function AlertsTab()    { return <Placeholder name="Alerts & Windows" /> }
function StepsTab()     { return <Placeholder name="Workflow Steps" /> }
function PostOpTab()    { return <Placeholder name="Post-Op Schedules" /> }
function CapacityTab()  { return <Placeholder name="Facilities & Capacity" /> }
function TemplatesTab() { return <Placeholder name="Templates" /> }
