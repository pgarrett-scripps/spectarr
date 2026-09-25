import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, NavLink, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { externalApi, type ExternalEntry, type ExternalLocation, type ExternalPage, type ExternalRoot, type ExternalTask } from '../api/external'
import { useAuth } from '../auth/AuthContext'
import { formatBytes } from '../components/Data'
import { ApiErrorBanner, EmptyState, LoadingState, PageHeader, Panel } from '../components/Page'
import type { Experiment, InstrumentAgent } from '../types'

const isPending = (task: ExternalTask) => task.state === 'pending'
const words = (value: string) => value.replaceAll('_', ' ')
function locationStatus(loc: ExternalLocation) {
  if (!loc.enabled) return 'Tracking paused'
  if (loc.root_status === 'unavailable') return 'Location unavailable'
  if (loc.agent_freshness === 'stale') return 'Agent offline or stale'
  if (loc.root_freshness === 'stale' || loc.freshness === 'stale') return 'Last observation is stale'
  if (loc.root_status === 'partial') return 'Scan incomplete'
  return loc.status === 'not_found' ? 'Not found at this location' : 'Observed at this location'
}

export function ExternalFiles() {
  const { projectId = '' } = useParams()
  return <ExternalInventory key={projectId} projectId={projectId} />
}

export function ExternalInventory({ projectId }: { projectId: string }) {
  const auth = useAuth()
  const admin = auth.user?.role === 'admin'
  const canEdit = admin || auth.user?.role === 'operator'
  const [roots, setRoots] = useState<ExternalRoot[]>([])
  const [page, setPage] = useState<ExternalPage>({ items: [], total: 0, next_cursor: null })
  const [tasks, setTasks] = useState<Record<string, ExternalTask[]>>({})
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [agents, setAgents] = useState<InstrumentAgent[]>([])
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [cursor, setCursor] = useState('')
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ label: '', path: '', agent_id: '' })
  const running = useRef(false)
  const generation = useRef(0)
  const load = useCallback(async () => {
    const current = ++generation.current
    try {
      const [r, p, e] = await Promise.all([externalApi.roots(projectId), externalApi.search(projectId, search, cursor), api.experiments(projectId)])
      const t = await Promise.all(r.items.map(async root => [root.id, (await externalApi.tasks(root.id)).items] as const))
      if (current !== generation.current) return
      setRoots(r.items)
      setPage(p)
      setExperiments(e)
      setTasks(Object.fromEntries(t))
      setError('')
    } catch (cause) {
      if (current === generation.current) setError(cause instanceof Error ? cause.message : 'Unable to load inventory')
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [projectId, search, cursor])
  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => { generation.current += 1
      window.clearInterval(timer)
    }
  }, [load])
  useEffect(() => {
    if (admin) void api.agents().then(setAgents).catch(cause => setError(String(cause)))
  }, [admin])
  const action = async (work: () => Promise<unknown>, message: string) => {
    if (running.current) return
    running.current = true
    setBusy(true)
    setNotice('')
    try {
      await work()
      await load()
      setNotice(message)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Operation failed')
    } finally {
      running.current = false
      setBusy(false)
    }
  }
  const entry = page.items.find(item => item.id === selected)
  return <>
    <nav className="breadcrumb" aria-label="Breadcrumb"><Link to="/projects">Projects</Link><span>External files</span></nav>
    <PageHeader title="External files" description="Find acquisitions in registered folders. Files stay on their original machine until you choose to import them." actions={<button className="button button-secondary" disabled={busy} onClick={() => void load()}>Refresh</button>} />
    <nav className="section-tabs" aria-label="Project sections">
      <NavLink to={`/projects/${projectId}/runs`}>Runs</NavLink>
      <NavLink to={`/projects/${projectId}/metadata`}>Metadata and SDRF</NavLink>
      <NavLink to={`/projects/${projectId}/external`} className="active">External files</NavLink>
    </nav>
    {error && <ApiErrorBanner message={error} onRetry={() => void load()} />}
    {notice && <p role="status">{notice}</p>}
    <Panel title="Registered folders" subtitle="Scans are requested explicitly. External file contents are not included in MassSpec backups.">
      {roots.map(root => <div className="external-root" key={root.id}>
        <div><strong>{root.label}</strong><p><code>{root.path}</code></p><small>{root.enabled ? words(root.status) : 'Tracking paused'} · {root.freshness} observation</small>{root.error && <p role="status">{root.error}</p>}</div>
        <div className="page-actions">
          {canEdit && <button className="button button-secondary" disabled={busy || !root.enabled || Boolean(tasks[root.id]?.find(isPending))} onClick={() => void action(() => externalApi.queue(root.id, 'scan'), 'Scan requested. The assigned agent will report progress.')}>Scan {root.label}</button>}
          {admin && <button className="button button-ghost" disabled={busy} onClick={() => void action(() => externalApi.update(root.id, !root.enabled), root.enabled ? 'Tracking paused. Pending requests canceled.' : 'Tracking resumed.')}>{root.enabled ? 'Pause' : 'Resume'}</button>}
        </div>
        <details><summary>Folder status and recent operations</summary>
          <p>Root ID: <code>{root.id}</code>. Agent ID: <code>{root.agent_id}</code>. The agent must run in catalog mode with this exact folder in its local allowlist.</p>
          {admin && <button className="button button-secondary" disabled={busy} onClick={() => void action(() => externalApi.update(root.id, root.enabled, true), 'Folder identity reset. Request a new scan after checking the mounted folder.')}>Revalidate folder identity</button>}
          {(tasks[root.id] ?? []).slice(0, 5).map(t => <p key={t.id}>{words(t.kind)}: {words(t.state)} {t.error}{canEdit && t.state === 'pending' && <button className="button button-ghost" disabled={busy} onClick={() => void action(() => externalApi.cancel(t.id), 'Request canceled. An upload already in progress may still complete.')}>Cancel request</button>}</p>)}
        </details>
      </div>)}
      {admin && <details className="external-root"><summary>Register a folder</summary>
        <p>Use a Linux agent in catalog mode. Configure the folder locally before scanning. Registration does not give the agent access to additional folders.</p>
        <form className="external-form" onSubmit={event => { event.preventDefault()
          void action(() => externalApi.register({ ...form, project_id: projectId }), 'Folder registered. Request a scan when the agent is ready.')
        }}>
          <label>Folder label<input required value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} /></label>
          <label>Path on the agent machine<input required placeholder="/mnt/research/archive" value={form.path} onChange={e => setForm({ ...form, path: e.target.value })} /></label>
          <label>Assigned agent<select required value={form.agent_id} onChange={e => setForm({ ...form, agent_id: e.target.value })}><option value="">Choose an agent</option>{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
          <button type="submit" className="button button-secondary" disabled={busy}>Register folder</button>
        </form>
      </details>}
    </Panel>
    <Panel title="Acquisitions" subtitle={`${page.total} external entries. Matching contents can belong to different acquisitions.`}>
      <form className="external-search" onSubmit={e => { e.preventDefault()
        setCursor('')
        setSelected('')
        setSearch(query)
      }}><label>Search external files<input value={query} onChange={e => setQuery(e.target.value)} placeholder="Filename, path, ID or SHA-256" /></label><button type="submit" className="button button-secondary">Search</button></form>
      {loading ? <LoadingState /> : page.items.length === 0 ? <EmptyState title="No external files found" description="Register a folder and request a scan, or adjust your search." /> : <div className="table-scroll" role="region" aria-label="External acquisition table" tabIndex={0}><table className="data-table"><thead><tr><th>Acquisition</th><th>Format</th><th>Size</th><th>Last known location</th><th>Observed</th></tr></thead><tbody>{page.items.map(item => {
        const loc = item.locations.find(l => l.status === 'observed') ?? item.locations[0]
        return <tr key={item.id}><td><button id={`external-entry-${item.id}`} className="button button-ghost" onClick={() => setSelected(item.id)}>{item.name}</button></td><td>{item.format}</td><td>{loc ? formatBytes(loc.byte_size) : 'Unknown'}</td><td>{loc && <><span>{loc.root_label}</span><br /><small>{locationStatus(loc)}</small></>}</td><td>{loc ? new Date(loc.observed_at).toLocaleString() : 'Unknown'}</td></tr>
      })}</tbody></table></div>}
      <div className="page-actions">{cursor && <button className="button button-secondary" onClick={() => { setCursor('')
        setSelected('')
      }}>First page</button>}{page.next_cursor && <button className="button button-secondary" onClick={() => { setCursor(page.next_cursor ?? '')
        setSelected('')
      }}>Next page</button>}</div>
    </Panel>
    {entry && <ExternalDetail key={entry.id} entry={entry} experiments={experiments} canEdit={!!canEdit} busy={busy} action={action} onClose={() => {
      setSelected('')
      document.getElementById(`external-entry-${entry.id}`)?.focus()
    }} />}
  </>
}

function ExternalDetail({ entry, experiments, canEdit, busy, action, onClose }: { entry: ExternalEntry, experiments: Experiment[], canEdit: boolean, busy: boolean, onClose: () => void, action: (work: () => Promise<unknown>, message: string) => Promise<void> }) {
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => panel.current?.focus(), [])
  const [experimentId, setExperimentId] = useState('')
  const [matches, setMatches] = useState<ExternalEntry[]>([])
  const [history, setHistory] = useState<string[]>([])
  return <div ref={panel} role="region" aria-label={`Details for ${entry.name}`} tabIndex={-1}><Panel title={entry.name} subtitle={`External acquisition · ${entry.id}`} actions={<button className="button button-ghost" onClick={onClose}>Close details</button>}>
    {entry.locations.map(loc => <div className="external-root" key={loc.id}>
      <p><strong>{locationStatus(loc)}</strong> · {words(loc.readiness)}</p>
      <p>Machine: {loc.host} · Folder: {loc.root_label}</p>
      <code className="external-path">{loc.root_path}/{loc.relative_path}</code>
      <p>This path belongs to the named machine. Map this folder explicitly before using it on another computer or inside Docker.</p>
      <p>{loc.verified_at ? `Last content verification: ${new Date(loc.verified_at).toLocaleString()}` : 'Content has not been verified for this observation.'}</p>
      {loc.sha256 && <details><summary>Recorded content checksum</summary><code className="external-path">{loc.sha256}</code><p>A recorded checksum does not guarantee the file is unchanged now.</p></details>}
      {canEdit && loc.enabled && loc.status === 'observed' && <div className="external-form">
        <button className="button button-secondary" disabled={busy} onClick={() => void action(() => externalApi.queue(loc.root_id, 'verify', { location_id: loc.id }), 'Verification requested. The agent waits for readiness before hashing.')}>Verify content</button>
        {loc.revision_id && <><label>Import into experiment<select value={experimentId} onChange={e => setExperimentId(e.target.value)}><option value="">Choose an experiment</option>{experiments.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label><button className="button button-primary" disabled={busy || !experimentId} onClick={() => void action(() => externalApi.queue(loc.root_id, 'import', { location_id: loc.id, revision_id: loc.revision_id, experiment_id: experimentId }), 'Import requested for this verified revision. The original files remain in place.')}>Import verified revision</button></>}
      </div>}
      {canEdit && loc.status === 'not_found' && loc.revision_id && <>
        <button className="button button-secondary" disabled={busy} onClick={() => void action(async () => setMatches((await externalApi.matches(entry.id)).items), 'Content matches loaded. Confirm only if this is the same acquisition that moved.')}>Find matching locations</button>
        {matches.flatMap(candidate => candidate.locations.filter(l => l.status === 'observed' && l.revision_id).map(other => <div key={other.id}><p>{candidate.name}: {other.root_label}/{other.relative_path}</p><button className="button button-secondary" disabled={busy} onClick={() => void action(() => externalApi.relocate(loc, other), 'Relocation confirmed. Both location histories are retained.')}>Confirm this acquisition moved here</button></div>))}
      </>}
    </div>)}
    <details className="external-root"><summary>Recorded history</summary><button className="button button-secondary" disabled={busy} onClick={() => void action(async () => setHistory((await externalApi.history(entry.id)).items.map(item => `${new Date(item.created_at).toLocaleString()}: ${JSON.stringify(item.facts)}`)), 'Loaded up to 50 history records.')}>Load history</button>{history.map((line, index) => <p className="external-path" key={index}>{line}</p>)}</details>
  </Panel></div>
}
