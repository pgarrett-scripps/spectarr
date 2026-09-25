import { FlaskConical } from 'lucide-react'
import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import { Layout } from './components/Layout'
import { ActivityPage } from './pages/ActivityPage'
import { Agents } from './pages/Agents'
import { Automation } from './pages/Automation'
import { Integrations } from './pages/Integrations'
import { ImportRun } from './pages/ImportRun'
import { ExternalFiles } from './pages/ExternalFiles'
import { Projects } from './pages/Library'
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
  if (auth.loading) return <div className="app-loading"><span className="brand-mark"><FlaskConical size={18} /></span>Loading MassSpec</div>
  if (!auth.user) return <Login />
  return <Layout><Routes>
    <Route path="/" element={<Overview />} />
    <Route path="/projects" element={<Projects />} />
    <Route path="/projects/:projectId/runs" element={<Runs />} />
    <Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} />
    <Route path="/library" element={<Navigate to="/projects" replace />} />
    <Route path="/runs" element={<Runs />} />
    <Route path="/inbox" element={<Runs inbox />} />
    <Route path="/runs/import" element={auth.user.role === 'admin' || auth.user.role === 'operator' ? <ImportRun /> : <Navigate to="/projects" replace />} />
    <Route path="/runs/:runId/:tab?" element={<RunDetail />} />
    <Route path="/projects/:projectId/external" element={<ExternalFiles />} />
    <Route path="/projects/:projectId/metadata" element={<ProjectMetadata />} />
    <Route path="/activity" element={<ActivityPage />} />
    <Route path="/processing" element={<Processing />} />
    <Route path="/agents" element={<AdminPage><Agents /></AdminPage>} />
    <Route path="/automation" element={<AdminPage><Automation /></AdminPage>} />
    <Route path="/storage" element={<Storage />} />
    <Route path="/integrations" element={<Integrations />} />
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="*" element={<NotFound />} />
  </Routes></Layout>
}

function AdminPage({ children }: { children: ReactNode }) {
  const auth = useAuth()
  return auth.user?.role === 'admin' ? children : <Navigate to="/" replace />
}
