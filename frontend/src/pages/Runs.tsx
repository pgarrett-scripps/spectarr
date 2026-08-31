import { ChevronRight, Download, FlaskConical, FolderInput, ListTree, Plus, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, NavLink, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatBytes, formatRelativeDate, RunStatusBadge } from '../components/Data'
import { ManageExperimentsDialog } from '../components/ManageExperimentsDialog'
import { MoveRunsDialog } from '../components/MoveRunsDialog'
import { ApiErrorBanner, EmptyState, LoadingState, PageHeader, Panel } from '../components/Page'
import { ProcessDialog } from '../components/ProcessDialog'
import { importRunPath, projectRunsPath, runPath } from '../navigation'
import type { Project } from '../types'

export function Runs({ inbox = false }: { inbox?: boolean }) {
  const { projectId = '' } = useParams()
  const resource = useResource(inbox ? api.inbox : api.runs, [])
  const project = useResource<Project | null>(() => projectId ? api.project(projectId) : Promise.resolve(null), null, projectId)
  const experiments = useResource(() => projectId ? api.experiments(projectId) : Promise.resolve([]), [], projectId)
  const auth = useAuth()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [moving, setMoving] = useState<string[] | null>(null)
  const [processing, setProcessing] = useState<{ projectId?: string, runIds?: string[] } | null>(null)
  const [managingExperiments, setManagingExperiments] = useState(false)
  const canMove = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  const canProcess = !inbox && canMove
  const experimentId = projectId ? searchParams.get('experiment') ?? '' : ''

  useEffect(() => setQuery(searchParams.get('q') ?? ''), [searchParams])

  const projectRuns = resource.data.filter(run => !projectId || run.projectId === projectId)
  const visibleRuns = projectRuns.filter(run => {
    const matchesExperiment = !experimentId || run.experimentId === experimentId
    const searchable = `${run.name} ${run.projectName} ${run.experimentName} ${run.sampleName} ${run.instrument}`.toLowerCase()
    return matchesExperiment && searchable.includes(query.trim().toLowerCase())
  })
  const selectedVisible = visibleRuns.filter(run => selected.has(run.id))
  const selectedExperiment = experiments.data.find(experiment => experiment.id === experimentId)
  const initialLoading = resource.loading || Boolean(projectId && (project.loading || experiments.loading))
  const hasFilters = Boolean(query.trim() || experimentId)

  useEffect(() => {
    if (experiments.loading || !experimentId || selectedExperiment) return
    const next = new URLSearchParams(searchParams)
    next.delete('experiment')
    setSearchParams(next, { replace: true })
  }, [experimentId, experiments.loading, searchParams, selectedExperiment, setSearchParams])

  const chooseExperiment = (id: string) => {
    const next = new URLSearchParams(searchParams)
    if (id) next.set('experiment', id)
    else next.delete('experiment')
    setSearchParams(next, { replace: true })
    setSelected(new Set())
  }

  const clearFilters = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('experiment')
    next.delete('q')
    setSearchParams(next, { replace: true })
    setQuery('')
    setSelected(new Set())
  }

  const toggle = (id: string) => setSelected(current => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const exportCsv = () => {
    const quote = (value: string | number) => `"${String(value).replaceAll('"', '""')}"`
    const rows = [
      ['Run', 'Project', 'Experiment', 'Sample', 'Instrument', 'Source', 'Bytes', 'Imported'],
      ...visibleRuns.map(run => [run.name, run.projectName, run.experimentName, run.sampleName, run.instrument, run.sourceFormat, run.sizeBytes, run.importedAt])
    ]
    const csv = rows.map(row => row.map(quote).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'spectarr-runs.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const title = inbox ? 'Instrument Inbox' : projectId ? project.data?.name ?? 'Project runs' : 'All runs'
  const description = inbox
    ? 'Review automatic instrument uploads and assign them to scientific experiments.'
    : projectId
      ? project.data?.description || 'Browse and manage every run in this project.'
      : 'Search acquisitions across every project.'

  return <>
    {projectId && <nav className="breadcrumb" aria-label="Breadcrumb"><Link to="/projects">Projects</Link><ChevronRight size={13} /><span>{project.data?.name ?? 'Project'}</span></nav>}
    <PageHeader title={title} description={description} actions={<>
      {auth.user?.role === 'admin' && projectId && <button className="button button-secondary" onClick={() => setManagingExperiments(true)}><ListTree size={16} /> Manage experiments</button>}
      {canProcess && projectId && <button className="button button-primary" onClick={() => setProcessing({ projectId })}><FlaskConical size={16} /> Process project</button>}
      {!inbox && visibleRuns.length > 0 && <button className="button button-secondary" onClick={exportCsv}><Download size={16} /> Export CSV</button>}
      {!inbox && <Link className="button button-primary" to={importRunPath(projectId || undefined)}><Plus size={16} /> Import run</Link>}
    </>} />
    {projectId && <nav className="section-tabs" aria-label="Project sections">
      <NavLink to={projectRunsPath(projectId)} className={({ isActive }) => isActive ? 'active' : ''}>Runs</NavLink>
      <NavLink to={`/projects/${projectId}/metadata`} className={({ isActive }) => isActive ? 'active' : ''}>Metadata and SDRF</NavLink>
    </nav>}
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {project.error && <ApiErrorBanner message={project.error} onRetry={project.refresh} />}
    {experiments.error && <ApiErrorBanner message={experiments.error} onRetry={experiments.refresh} />}

    {initialLoading ? <LoadingState label={projectId ? 'Loading project runs' : 'Loading runs'} /> : <>{projectId && experiments.data.length > 0 && <div className="experiment-filter" aria-label="Filter runs by experiment">
      <span>Experiments</span>
      <button className={!experimentId ? 'active' : ''} onClick={() => chooseExperiment('')}>All <small>{projectRuns.length}</small></button>
      {experiments.data.map(experiment => <button className={experiment.id === experimentId ? 'active' : ''} key={experiment.id} onClick={() => chooseExperiment(experiment.id)}>{experiment.name} <small>{projectRuns.filter(run => run.experimentId === experiment.id).length}</small></button>)}
    </div>}

    <Panel className="table-panel">
      <div className="table-toolbar">
        <div className="table-search"><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter runs..." aria-label="Filter runs" /></div>
        {selectedExperiment && <button className="button button-secondary" onClick={() => chooseExperiment('')}>Clear {selectedExperiment.name}</button>}
        {canProcess && selectedVisible.length > 0 && <button className="button button-primary" onClick={() => setProcessing({ runIds: selectedVisible.map(run => run.id) })}><FlaskConical size={15} /> Process selected ({selectedVisible.length})</button>}
        {canMove && selectedVisible.length > 0 && <button className="button button-primary" onClick={() => setMoving(selectedVisible.map(run => run.id))}><FolderInput size={15} /> Move selected ({selectedVisible.length})</button>}
      </div>
      {visibleRuns.length === 0 ? inbox
        ? <EmptyState title="Inbox is clear" description="New automatic instrument uploads awaiting assignment will appear here." />
        : <EmptyState title="No runs found" description={hasFilters ? 'No runs match the current search and experiment filters.' : 'Import an instrument acquisition to add the first run.'} action={hasFilters ? 'Clear filters' : 'Import run'} onAction={() => hasFilters ? clearFilters() : navigate(importRunPath(projectId || undefined))} />
        : <div className="table-scroll"><table>
          <thead><tr>{canMove && <th className="select-column"><input type="checkbox" name="selectAllRuns" aria-label="Select all visible runs" checked={selectedVisible.length === visibleRuns.length && visibleRuns.length > 0} onChange={() => setSelected(selectedVisible.length === visibleRuns.length ? new Set() : new Set(visibleRuns.map(run => run.id)))} /></th>}<th>Run</th><th>Status</th><th>{projectId ? 'Experiment' : 'Project'}</th><th>Instrument</th><th>Source</th><th>Size</th><th>Imported</th><th /></tr></thead>
          <tbody>{visibleRuns.map(run => <tr key={run.id}>
            {canMove && <td className="select-column"><input type="checkbox" aria-label={`Select ${run.name}`} checked={selected.has(run.id)} onChange={() => toggle(run.id)} /></td>}
            <td><Link className="primary-cell" to={runPath(run)}><span className="file-glyph file-glyph-small">{run.sourceFormat}</span><span><strong>{run.name}</strong><small>{run.sampleName}</small></span></Link></td>
            <td><RunStatusBadge status={run.status} /></td>
            <td><span>{projectId ? run.experimentName : run.projectName}</span>{run.assignmentStatus === 'needs_assignment' && <small className="assignment-note">Needs assignment</small>}</td>
            <td className="muted-cell">{run.instrument}</td>
            <td><span className="format-chip">{run.sourceFormat}</span></td>
            <td>{formatBytes(run.sizeBytes)}</td>
            <td className="muted-cell">{formatRelativeDate(run.importedAt)}</td>
            <td><div className="row-actions">{canMove && <button className="icon-button" aria-label={`Move ${run.name}`} onClick={() => setMoving([run.id])}><FolderInput size={16} /></button>}<Link className="row-link" to={runPath(run)} aria-label={`Open ${run.name}`}><ChevronRight size={17} /></Link></div></td>
          </tr>)}</tbody>
        </table></div>}
      <div className="table-footer"><span>Showing {visibleRuns.length} of {projectRuns.length} runs</span></div>
    </Panel></>}
    {moving && <MoveRunsDialog runIds={moving} onClose={() => setMoving(null)} onMoved={() => {
      setMoving(null)
      setSelected(new Set())
      resource.refresh()
    }} />}
    {processing && <ProcessDialog projectId={processing.projectId} runIds={processing.runIds} onClose={() => setProcessing(null)} />}
    {managingExperiments && projectId && <ManageExperimentsDialog projectId={projectId} onClose={() => setManagingExperiments(false)} onDeleted={() => {
      resource.refresh()
      experiments.refresh()
    }} />}
  </>
}
