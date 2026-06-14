import { useNavigate } from 'react-router-dom'
import { SettingsCard } from './MyChecklist'


export default function ChecklistSettings() {
  const navigate = useNavigate()

  return (
    <div className="space-y-4">
      <h1 className="page-title">Checklist Settings</h1>
      <SettingsCard onClose={() => navigate('/checklist')} />
    </div>
  )
}
