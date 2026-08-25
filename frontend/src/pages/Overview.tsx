import { Activity, ArrowRight, Database, HardDrive, Plus, TestTubes, Workflow } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatBytes, formatRelativeDate, JobStatusBadge, ProgressBar, RunStatusBadge } from '../components/Data'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'
import type { OverviewData } from '../types'

export function Overview() {
  const resource = useResource<OverviewData | null>(api.overview, null)
  if (!resource.data) return <>
    <PageHeader eyebrow="Library overview" title="Spectarr overview" description="Live library and processing status from the Spectarr API." actions={<Link className="button button-primary" to="/runs/import"><Plus size={16} /> Import data</Link>} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
  </>
  const { data } = resource
  const totalBytes = data.storage.reduce((sum, item) => sum + item.usedBytes, 0)
  const activeJobs = data.jobs.filter(job => job.status === 'running' || job.status === 'queued')
  const formatTotal = Object.values(data.stats.formatCounts).reduce((sum, count) => sum + count, 0)
  const formatRows = Object.entries(data.stats.formatCounts)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 4)

  return (
    <>
      <PageHeader
        eyebrow="Library overview"
        title="Spectarr overview"
        description="Live library and processing status from the Spectarr API."
        actions={<Link className="button button-primary" to="/runs/import"><Plus size={16} /> Import data</Link>}
      />
      {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}

      <div className="metric-grid">
        <Metric icon={<TestTubes />} label="Total runs" value={data.stats.runs.toLocaleString()} meta="Managed acquisitions" tone="violet" />
        <Metric icon={<Database />} label="Artifacts" value={data.stats.artifacts.toLocaleString()} meta="Sources and derivatives" tone="cyan" />
        <Metric icon={<HardDrive />} label="Storage used" value={formatBytes(totalBytes)} meta="Logical artifact bytes" tone="blue" />
        <Metric icon={<Workflow />} label="Active jobs" value={String(activeJobs.length)} meta={`${data.health.workers} workers online`} tone="amber" />
      </div>

      <div className="overview-grid">
        <Panel title="Recent runs" subtitle="Latest data added to your library" actions={<Link className="text-link" to="/runs">View all <ArrowRight size={14} /></Link>}>
          <div className="run-list">
            {data.runs.slice(0, 4).map(run => (
              <Link className="run-list-item" to={`/runs/${run.id}`} key={run.id}>
                <div className="file-glyph">{run.sourceFormat}</div>
                <div className="run-list-primary"><strong>{run.name}</strong><span>{run.projectName} · {run.instrument}</span></div>
                <div className="run-list-size"><strong>{formatBytes(run.sizeBytes)}</strong><span>{formatRelativeDate(run.importedAt)}</span></div>
                <RunStatusBadge status={run.status} />
              </Link>
            ))}
          </div>
        </Panel>

        <Panel title="Processing queue" subtitle={`${activeJobs.length} active jobs`} actions={<Link className="text-link" to="/activity">Activity <ArrowRight size={14} /></Link>}>
          <div className="job-list">
            {data.jobs.slice(0, 4).map(job => (
              <div className="job-item" key={job.id}>
                <div className={`job-icon job-${job.kind}`}><Activity size={17} /></div>
                <div className="job-main">
                  <div className="job-title"><strong>{job.runName}</strong><JobStatusBadge status={job.status} /></div>
                  <span>{job.detail}</span>
                  {(job.status === 'running' || job.status === 'queued') && <ProgressBar value={job.progress} />}
                </div>
                <span className="job-percent">{job.status === 'running' ? `${job.progress}%` : ''}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="lower-grid">
        <Panel title="Storage" subtitle="Capacity across configured locations">
          <div className="storage-summary">
            {data.storage.map(location => {
              const percentage = Math.round(location.usedBytes / location.capacityBytes * 100)
              return <div className="storage-row" key={location.id}>
                <div className="storage-row-head"><strong>{location.name}</strong><span>{formatBytes(location.usedBytes)} of {formatBytes(location.capacityBytes)}</span></div>
                <ProgressBar value={percentage} tone={percentage > 75 ? 'amber' : 'blue'} />
              </div>
            })}
          </div>
        </Panel>
        <Panel title="Library mix" subtitle="Source formats across all runs">
          <div className="donut-layout">
            <div className="donut"><span><strong>{formatTotal}</strong>artifacts</span></div>
            <div className="legend">
              {formatRows.map(([format, count], index) => <span key={format}><i className={['legend-violet', 'legend-cyan', 'legend-blue', 'legend-muted'][index]} /> {format} <strong>{formatTotal ? Math.round(count / formatTotal * 100) : 0}%</strong></span>)}
              {formatRows.length === 0 && <span>No artifacts imported yet</span>}
            </div>
          </div>
        </Panel>
      </div>
    </>
  )
}

function Metric({ icon, label, value, meta, tone }: { icon: React.ReactNode, label: string, value: string, meta: string, tone: string }) {
  return <div className="metric-card">
    <div className={`metric-icon metric-${tone}`}>{icon}</div>
    <div><span>{label}</span><strong>{value}</strong><small>{meta}</small></div>
  </div>
}
