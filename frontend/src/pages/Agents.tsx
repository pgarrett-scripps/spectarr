import { Check, Clipboard, Plus, RadioTower, RotateCw, Server, Settings2, TriangleAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatRelativeDate } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader, Panel } from '../components/Page'
import type { InstrumentAgent } from '../types'
import { resolveProjectId } from './agentDestination'

export function Agents() {
  const resource = useResource(api.agents, [])
  const projects = useResource(api.projects, [])
  const experiments = useResource(() => api.experiments(), [])
  const [creating, setCreating] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [enrollment, setEnrollment] = useState<{ id: string, token: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const [registrationMode, setRegistrationMode] = useState<'inbox' | 'direct'>('inbox')
  const [registrationProjectId, setRegistrationProjectId] = useState('')
  const [registrationExperimentId, setRegistrationExperimentId] = useState('')
  const [configuring, setConfiguring] = useState<InstrumentAgent | null>(null)
  const [configurationMode, setConfigurationMode] = useState<'inbox' | 'direct'>('inbox')
  const [configurationProjectId, setConfigurationProjectId] = useState('')
  const [configurationExperimentId, setConfigurationExperimentId] = useState('')
  const scientificProjects = projects.data.filter(project => !project.systemKey)
  const registrationSelectedProjectId = resolveProjectId(registrationProjectId, scientificProjects)
  const registrationSelectedExperimentId = registrationExperimentId || experiments.data.find(item => item.projectId === registrationSelectedProjectId)?.id || ''
  const configurationSelectedProjectId = resolveProjectId(configurationProjectId, scientificProjects)
  const configurationSelectedExperimentId = configurationExperimentId || experiments.data.find(item => item.projectId === configurationSelectedProjectId)?.id || ''

  const register = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setSubmitting(true)
    setError(null)
    try {
      if (registrationMode === 'direct' && !registrationSelectedExperimentId) throw new Error('Choose a destination experiment')
      const result = await api.registerAgent(String(form.get('name')), registrationMode === 'direct' ? registrationSelectedExperimentId : undefined)
      setEnrollment({ id: result.agent.id, token: result.token })
      resource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not register the agent')
    } finally {
      setSubmitting(false)
    }
  }

  const openConfiguration = (agent: InstrumentAgent) => {
    const experiment = experiments.data.find(item => item.id === agent.destinationExperimentId)
    setConfiguring(agent)
    setConfigurationMode(agent.destinationMode)
    setConfigurationProjectId(agent.destinationMode === 'direct' ? resolveProjectId(experiment?.projectId ?? '', scientificProjects) : scientificProjects[0]?.id ?? '')
    setConfigurationExperimentId(agent.destinationMode === 'direct' ? agent.destinationExperimentId ?? '' : '')
    setError(null)
  }

  const saveConfiguration = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!configuring) return
    if (configurationMode === 'direct' && !configurationSelectedExperimentId) {
      setError('Choose a destination experiment')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.updateAgentDestination(configuring.id, configurationMode, configurationMode === 'direct' ? configurationSelectedExperimentId : undefined)
      setConfiguring(null)
      resource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update the destination')
    } finally {
      setSubmitting(false)
    }
  }

  return <>
    <PageHeader title="Instrument agents" description="Monitor acquisition-folder watchers and their upload queues." actions={<><button className="button button-secondary" onClick={resource.refresh}><RotateCw size={15} /> Refresh</button><button className="button button-primary" onClick={() => {
      setEnrollment(null)
      setCopied(false)
      setRegistrationMode('inbox')
      setRegistrationProjectId(scientificProjects[0]?.id ?? '')
      setRegistrationExperimentId('')
      setError(null)
      setCreating(true)
    }}><Plus size={16} /> Register agent</button></>} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {projects.error && <ApiErrorBanner message={`Projects: ${projects.error}`} onRetry={projects.refresh} />}
    {experiments.error && <ApiErrorBanner message={`Experiments: ${experiments.error}`} onRetry={experiments.refresh} />}
    {resource.data.length === 0 ? <EmptyState title="No instrument agents" description="Register an agent, then install it on an acquisition computer or gateway." action="Register agent" onAction={() => {
      setEnrollment(null)
      setCopied(false)
      setError(null)
      setCreating(true)
    }} /> : <div className="agent-grid">{resource.data.map(agent => <Panel key={agent.id} className="agent-card">
      <div className="agent-card-head"><span className="storage-large-icon"><RadioTower size={20} /></span><span className={`health health-${agent.status === 'online' ? 'healthy' : agent.status}`}><i />{agent.status}</span></div>
      <h2>{agent.name}</h2><p>{agent.platform} · version {agent.version}</p>
      <dl className="agent-stats"><div><dt>Destination</dt><dd>{destinationLabel(agent, experiments.data, projects.data)}</dd></div><div><dt>Backlog</dt><dd>{agent.backlog} acquisitions</dd></div><div><dt>Last seen</dt><dd>{agent.lastSeenAt ? formatRelativeDate(agent.lastSeenAt) : 'Never'}</dd></div><div><dt>Watch paths</dt><dd>{agent.watchPaths.join(', ') || 'Configured on agent'}</dd></div></dl>
      <button className="button button-ghost button-small agent-configure" onClick={() => openConfiguration(agent)}><Settings2 size={14} /> Configure destination</button>
      {agent.lastError && <div className="agent-error"><TriangleAlert size={14} />{agent.lastError}</div>}
    </Panel>)}</div>}
    <Panel title="How ingestion works" subtitle="The agent never modifies active acquisition files">
      <div className="agent-flow"><span><Server />Watch</span><i>→</i><span><Check />Stability check</span><i>→</i><span><RadioTower />Resumable upload</span><i>→</i><span><Check />Verify and extract</span></div>
    </Panel>
    {creating && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="register-agent-title">
      <div className="modal-header"><div><h2 id="register-agent-title">Register instrument agent</h2><p>The enrollment token is shown once.</p></div><button className="icon-button" aria-label="Close" onClick={() => setCreating(false)}>×</button></div>
      {enrollment ? <div className="token-result"><strong>Agent enrollment</strong><p>Configure both values on the acquisition computer. The token is shown only once. Its upload destination is managed in Spectarr.</p><div className="endpoint enrollment-config"><code>{`SPECTARR_AGENT_ID=${enrollment.id}\nSPECTARR_AGENT_TOKEN=${enrollment.token}`}</code><button className="icon-button" aria-label="Copy agent enrollment" onClick={() => {
        setError(null)
        void navigator.clipboard.writeText(`SPECTARR_AGENT_ID=${enrollment.id}\nSPECTARR_AGENT_TOKEN=${enrollment.token}`).then(() => setCopied(true)).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not copy to the clipboard'))
      }}>{copied ? <Check size={15} /> : <Clipboard size={15} />}</button></div>{error && <div className="modal-error" role="alert">{error}</div>}<button className="button button-primary" onClick={() => setCreating(false)}>Done</button></div> : <form onSubmit={event => void register(event)}>
        <div className="modal-fields"><label><span>Name</span><input name="name" required autoFocus placeholder="Orbitrap acquisition PC" /></label><DestinationFields mode={registrationMode} onMode={setRegistrationMode} projectId={registrationProjectId} onProject={value => {
          setRegistrationProjectId(value)
          setRegistrationExperimentId('')
        }} experimentId={registrationExperimentId} onExperiment={setRegistrationExperimentId} projects={scientificProjects} experiments={experiments.data} /></div>
        {error && <div className="modal-error" role="alert">{error}</div>}
        <div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setCreating(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting || (registrationMode === 'direct' && !registrationSelectedExperimentId)}>{submitting ? 'Registering' : 'Register agent'}</button></div>
      </form>}
    </section></div>}
    {configuring && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="configure-agent-title">
      <div className="modal-header"><div><h2 id="configure-agent-title">Configure {configuring.name}</h2><p>New acquisitions use this destination. Existing runs are unchanged.</p></div><button className="icon-button" aria-label="Close" onClick={() => setConfiguring(null)}>×</button></div>
      <form onSubmit={event => void saveConfiguration(event)}><div className="modal-fields"><DestinationFields mode={configurationMode} onMode={setConfigurationMode} projectId={configurationProjectId} onProject={value => {
        setConfigurationProjectId(value)
        setConfigurationExperimentId('')
      }} experimentId={configurationExperimentId} onExperiment={setConfigurationExperimentId} projects={scientificProjects} experiments={experiments.data} /></div>
      {error && <div className="modal-error" role="alert">{error}</div>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setConfiguring(null)}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting || (configurationMode === 'direct' && !configurationSelectedExperimentId)}>{submitting ? 'Saving' : 'Save destination'}</button></div></form>
    </section></div>}
  </>
}

function DestinationFields({ mode, onMode, projectId, onProject, experimentId, onExperiment, projects, experiments }: {
  mode: 'inbox' | 'direct'
  onMode: (mode: 'inbox' | 'direct') => void
  projectId: string
  onProject: (id: string) => void
  experimentId: string
  onExperiment: (id: string) => void
  projects: Array<{ id: string, name: string }>
  experiments: Array<{ id: string, projectId: string, name: string }>
}) {
  const selectedProjectId = resolveProjectId(projectId, projects)
  const availableExperiments = experiments.filter(experiment => experiment.projectId === selectedProjectId)
  const selectedExperimentId = experimentId && availableExperiments.some(item => item.id === experimentId) ? experimentId : availableExperiments[0]?.id || ''
  return <>
    <label><span>Upload destination</span><select aria-label="Upload destination" value={mode} onChange={event => onMode(event.target.value as 'inbox' | 'direct')}><option value="inbox">Agent inbox, assign later</option><option value="direct">Existing experiment</option></select></label>
    {mode === 'direct' && <><label><span>Project</span><select aria-label="Agent destination project" value={selectedProjectId} onChange={event => onProject(event.target.value)}>{projects.length === 0 && <option value="">No projects available</option>}{projects.map(project => <option value={project.id} key={project.id}>{project.name}</option>)}</select></label><label><span>Experiment</span><select aria-label="Agent destination experiment" value={selectedExperimentId} onChange={event => onExperiment(event.target.value)}>{availableExperiments.length === 0 && <option value="">No experiments available</option>}{availableExperiments.map(experiment => <option value={experiment.id} key={experiment.id}>{experiment.name}</option>)}</select></label></>}
  </>
}


function destinationLabel(agent: InstrumentAgent, experiments: Array<{ id: string, projectId: string, name: string }>, projects: Array<{ id: string, name: string }>) {
  const experiment = experiments.find(item => item.id === agent.destinationExperimentId)
  const project = projects.find(item => item.id === experiment?.projectId)
  if (!experiment) return agent.destinationMode === 'inbox' ? 'Agent inbox' : 'Not configured'
  return agent.destinationMode === 'inbox' ? `Inbox · ${experiment.name}` : `${project?.name ?? 'Project'} · ${experiment.name}`
}
