import { FlaskConical } from 'lucide-react'
import { Route, Routes } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import { Layout } from './components/Layout'
import { ActivityPage } from './pages/ActivityPage'
import { Agents } from './pages/Agents'
import { Automation } from './pages/Automation'
import { Integrations } from './pages/Integrations'
import { ImportRun } from './pages/ImportRun'
import { Library } from './pages/Library'
import { Login } from './pages/Login'
import { NotFound } from './pages/NotFound'
import { Overview } from './pages/Overview'
import { Processing } from './pages/Processing'
import { ProjectMetadata } from './pages/ProjectMetadata'
import { RunDetail } from './pages/RunDetail'
import { Runs } from './pages/Runs'
import { SettingsPage } from './pages/SettingsPage'
import { Storage } from './pages/Storage'

export default function App() {
  const auth = useAuth()
  if (auth.loading) return <div className="app-loading"><span className="brand-mark"><FlaskConical size={18} /></span>Loading Spectarr</div>
  if (!auth.user) return <Login />
  return <Layout><Routes>
    <Route path="/" element={<Overview />} />
    <Route path="/library" element={<Library />} />
    <Route path="/runs" element={<Runs />} />
    <Route path="/inbox" element={<Runs inbox />} />
    <Route path="/runs/import" element={<ImportRun />} />
    <Route path="/runs/:runId" element={<RunDetail />} />
    <Route path="/projects/:projectId/metadata" element={<ProjectMetadata />} />
    <Route path="/activity" element={<ActivityPage />} />
    <Route path="/processing" element={<Processing />} />
    <Route path="/agents" element={<Agents />} />
    <Route path="/automation" element={<Automation />} />
    <Route path="/storage" element={<Storage />} />
    <Route path="/integrations" element={<Integrations />} />
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="*" element={<NotFound />} />
  </Routes></Layout>
}
