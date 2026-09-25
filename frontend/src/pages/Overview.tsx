import { ArrowRight, Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatBytes, formatRelativeDate, JobStatusBadge, RunStatusBadge } from '../components/Data'
import { ApiErrorBanner, EmptyState, LoadingState, PageHeader, Panel } from '../components/Page'
import { projectRunsPath, runPath } from '../navigation'
import type { OverviewData } from '../types'

export function Overview() {
  const resource = useResource<OverviewData | null>(api.overview, null, undefined, 'overview')
  const auth = useAuth()
  const canImport = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  const data = resource.data
  const totalBytes = data?.storage.reduce((sum, item) => sum + item.usedBytes, 0) ?? 0
  const recentJobs = data?.jobs.slice(0, 4) ?? []

  return <>
    <PageHeader title="MassSpec overview" description="Your acquisitions, research projects, and recent processing."
      actions={canImport ? <Link className="button button-primary" to="/runs/import"><Plus size={16} /> Import data</Link> : undefined} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {!data && !resource.error && <LoadingState label="Loading library overview" />}
    {data && <>
      <dl className="library-summary" aria-label="Library summary">
        <div><dt>Acquisitions</dt><dd>{data.stats.runs.toLocaleString()}</dd></div>
        <div><dt>Files</dt><dd>{data.stats.artifacts.toLocaleString()}</dd><small>Sources and derivatives</small></div>
        <div><dt>Cataloged data</dt><dd>{formatBytes(totalBytes)}</dd><small>Logical file size</small></div>
        <div><dt>Processing</dt><dd>{data.health.queueDepth.toLocaleString()}</dd><small>Queued or running jobs</small></div>
      </dl>
      <div className="research-overview">
        <div>
          <Panel title="Recent acquisitions" subtitle="Most recently imported runs" actions={<Link className="text-link" to="/runs">All runs <ArrowRight size={14} /></Link>}>
            {data.runs.length > 0 ? <div className="table-scroll" role="region" aria-label="Recent acquisitions table" tabIndex={0}><table className="recent-runs-table">
              <caption className="sr-only">Recently imported acquisitions</caption>
              <thead><tr><th scope="col">Run</th><th scope="col">Project</th><th scope="col">Status</th><th scope="col">Imported</th></tr></thead>
              <tbody>{data.runs.slice(0, 6).map(run => <tr key={run.id}>
                <td><Link className="primary-cell" to={runPath(run)}><span><strong>{run.name}</strong><small>{run.sampleName} · {run.sourceFormat}</small></span></Link></td>
                <td>{run.projectId ? <Link className="quiet-link" to={projectRunsPath(run.projectId)}>{run.projectName}</Link> : run.projectName}</td>
                <td><RunStatusBadge status={run.status} /></td>
                <td className="muted-cell"><time dateTime={run.importedAt} title={new Date(run.importedAt).toLocaleString()}>{formatRelativeDate(run.importedAt)}</time></td>
              </tr>)}</tbody>
            </table></div> : <EmptyState title="Begin with a research project" description={canImport ? 'Create a project to group related experiments, then import your acquisitions.' : 'Projects and acquisitions shared with you will appear here.'} />}
          </Panel>
          <Panel title="Research projects" actions={<Link className="text-link" to="/projects">All projects <ArrowRight size={14} /></Link>}>
            {data.projects.filter(project => !project.systemKey).length ? <div className="project-register">{data.projects.filter(project => !project.systemKey).slice(0, 4).map(project => <Link key={project.id} to={projectRunsPath(project.id)}>
              <span><strong>{project.name}</strong><small>{project.description || 'No description'}</small></span><span>{project.runCount} {project.runCount === 1 ? 'run' : 'runs'}<ArrowRight size={14} /></span>
            </Link>)}</div> : <div className="panel-note">No research projects yet. <Link className="quiet-link" to="/projects">Open projects</Link></div>}
          </Panel>
        </div>
        <aside>
          <Panel title="Recent processing" subtitle={data.health.queueDepth ? `${data.health.queueDepth} jobs queued or running` : 'No jobs queued or running'} actions={<Link className="text-link" to="/processing">View <ArrowRight size={14} /></Link>}>
            {recentJobs.length ? <ol className="processing-register">{recentJobs.map(job => <li key={job.id}>
              <div><strong>{job.runName}</strong><JobStatusBadge status={job.status} /></div>
              <p>{job.detail === 'extract_metadata' ? 'Metadata and spectrum catalog' : job.detail}</p>
              <time dateTime={job.createdAt}>{formatRelativeDate(job.createdAt)}</time>
            </li>)}</ol> : <p className="panel-note">Extraction and conversion jobs will appear here after import.</p>}
          </Panel>
          <div className="workspace-note"><h2>A record of your research data</h2><p>Each run keeps its source files, generated formats, and provenance together.</p><Link className="quiet-link" to="/projects">Browse the library <ArrowRight size={14} /></Link></div>
        </aside>
      </div>
    </>}
  </>
}
