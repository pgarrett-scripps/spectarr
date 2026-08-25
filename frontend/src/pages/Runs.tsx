import { ChevronRight, Download, FileSpreadsheet, Filter, FlaskConical, FolderInput, ListTree, Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatBytes, formatRelativeDate, RunStatusBadge } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader, Panel } from '../components/Page'
import { MoveRunsDialog } from '../components/MoveRunsDialog'
import { ProcessDialog } from '../components/ProcessDialog'
import { ManageExperimentsDialog } from '../components/ManageExperimentsDialog'
import { useAuth } from '../auth/AuthContext'

export function Runs({ inbox = false }: { inbox?: boolean }) {
  const resource = useResource(inbox ? api.inbox : api.runs, [])
  const auth = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [moving, setMoving] = useState<string[] | null>(null)
  const [processing, setProcessing] = useState<{ projectId?: string, runIds?: string[] } | null>(null)
  const [managingExperiments, setManagingExperiments] = useState(false)
  const canMove = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  const canProcess = !inbox && canMove
  const projectId = searchParams.get('project')
  const visibleRuns = resource.data.filter(run => {
    const matchesProject = !projectId || run.projectId === projectId
    const searchable = `${run.name} ${run.projectName} ${run.sampleName} ${run.instrument}`.toLowerCase()
    return matchesProject && searchable.includes(query.trim().toLowerCase())
  })
  const selectedVisible = visibleRuns.filter(run => selected.has(run.id))
  const toggle = (id: string) => setSelected(current => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const exportCsv = () => {
    const quote = (value: string | number) => `"${String(value).replaceAll('"', '""')}"`
    const rows = [
      ['Run', 'Project', 'Sample', 'Instrument', 'Source', 'Bytes', 'Imported'],
      ...visibleRuns.map(run => [run.name, run.projectName, run.sampleName, run.instrument, run.sourceFormat, run.sizeBytes, run.importedAt])
    ]
    const csv = rows.map(row => row.map(quote).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'spectarr-runs.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }
  return <>
    <PageHeader title={inbox ? 'Instrument Inbox' : projectId ? visibleRuns[0]?.projectName ?? 'Project' : 'Runs'} description={inbox ? 'Review automatic instrument uploads and assign them to scientific experiments.' : projectId ? 'Process runs and maintain repository-ready project metadata.' : 'Browse, inspect, and manage every acquisition.'} actions={<>{projectId && <Link className="button button-secondary" to={`/projects/${projectId}/metadata`}><FileSpreadsheet size={16} /> Metadata and SDRF</Link>}{auth.user?.role === 'admin' && projectId && <button className="button button-secondary" onClick={() => setManagingExperiments(true)}><ListTree size={16} /> Manage experiments</button>}{canProcess && projectId && <button className="button button-primary" onClick={() => setProcessing({ projectId })}><FlaskConical size={16} /> Process project</button>}{!inbox && visibleRuns.length > 0 && <button className="button button-secondary" onClick={exportCsv}><Download size={16} /> Export CSV</button>}{!inbox && <Link className="button button-primary" to="/runs/import"><Plus size={16} /> Import run</Link>}</>} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <Panel className="table-panel">
      <div className="table-toolbar">
        <div className="table-search"><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter runs..." aria-label="Filter runs" /></div>
        {projectId && <button className="button button-secondary" onClick={() => navigate('/runs')}><Filter size={15} /> Clear project filter</button>}
        {canProcess && selectedVisible.length > 0 && <button className="button button-primary" onClick={() => setProcessing({ runIds: selectedVisible.map(run => run.id) })}><FlaskConical size={15} /> Process selected ({selectedVisible.length})</button>}
        {canMove && selectedVisible.length > 0 && <button className="button button-primary" onClick={() => setMoving(selectedVisible.map(run => run.id))}><FolderInput size={15} /> Move selected ({selectedVisible.length})</button>}
      </div>
      {visibleRuns.length === 0 ? inbox ? <EmptyState title="Inbox is clear" description="New automatic instrument uploads awaiting assignment will appear here." /> : <EmptyState title="No runs found" description="Import an instrument acquisition or adjust the current filters." action="Import run" onAction={() => navigate('/runs/import')} /> : (
        <div className="table-scroll"><table>
          <thead><tr>{canMove && <th className="select-column"><input type="checkbox" name="selectAllRuns" aria-label="Select all visible runs" checked={selectedVisible.length === visibleRuns.length && visibleRuns.length > 0} onChange={() => setSelected(selectedVisible.length === visibleRuns.length ? new Set() : new Set(visibleRuns.map(run => run.id)))} /></th>}<th>Run</th><th>Status</th><th>Project</th><th>Instrument</th><th>Source</th><th>Size</th><th>Imported</th><th /></tr></thead>
          <tbody>{visibleRuns.map(run => <tr key={run.id}>
            {canMove && <td className="select-column"><input type="checkbox" aria-label={`Select ${run.name}`} checked={selected.has(run.id)} onChange={() => toggle(run.id)} /></td>}
            <td><Link className="primary-cell" to={`/runs/${run.id}`}><span className="file-glyph file-glyph-small">{run.sourceFormat}</span><span><strong>{run.name}</strong><small>{run.sampleName}</small></span></Link></td>
            <td><RunStatusBadge status={run.status} /></td>
            <td><span>{run.projectName}</span>{run.assignmentStatus === 'needs_assignment' && <small className="assignment-note">Needs assignment</small>}</td>
            <td className="muted-cell">{run.instrument}</td>
            <td><span className="format-chip">{run.sourceFormat}</span></td>
            <td>{formatBytes(run.sizeBytes)}</td>
            <td className="muted-cell">{formatRelativeDate(run.importedAt)}</td>
            <td><div className="row-actions">{canMove && <button className="icon-button" aria-label={`Move ${run.name}`} onClick={() => setMoving([run.id])}><FolderInput size={16} /></button>}<Link className="row-link" to={`/runs/${run.id}`} aria-label={`Open ${run.name}`}><ChevronRight size={17} /></Link></div></td>
          </tr>)}</tbody>
        </table></div>
      )}
      <div className="table-footer"><span>Showing {visibleRuns.length} of {resource.data.length} runs</span></div>
    </Panel>
    {moving && <MoveRunsDialog runIds={moving} onClose={() => setMoving(null)} onMoved={() => {
      setMoving(null)
      setSelected(new Set())
      resource.refresh()
    }} />}
    {processing && <ProcessDialog projectId={processing.projectId} runIds={processing.runIds} onClose={() => setProcessing(null)} />}
    {managingExperiments && projectId && <ManageExperimentsDialog projectId={projectId} onClose={() => setManagingExperiments(false)} onDeleted={resource.refresh} />}
  </>
}
