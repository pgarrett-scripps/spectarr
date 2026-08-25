import { Activity as ActivityIcon, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatRelativeDate, JobStatusBadge, ProgressBar } from '../components/Data'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'

export function ActivityPage() {
  const resource = useResource(api.jobs, [])
  const [actionError, setActionError] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'active' | 'history'>('all')
  const visibleJobs = resource.data.filter(job => filter === 'all'
    || (filter === 'active' ? ['running', 'queued'].includes(job.status) : ['complete', 'failed'].includes(job.status)))
  const retry = async (jobId: string) => {
    setActionError(null)
    try {
      await api.retryJob(jobId)
      resource.refresh()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not retry the job')
    }
  }
  const running = resource.data.filter(job => job.status === 'running').length
  const queued = resource.data.filter(job => job.status === 'queued').length
  const failed = resource.data.filter(job => job.status === 'failed').length
  return <>
    <PageHeader title="Activity" description="Follow imports, conversions, validation, and indexing jobs." actions={<button className="button button-secondary" onClick={resource.refresh}><RotateCcw size={16} /> Refresh</button>} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {actionError && <div className="message-banner" role="alert">{actionError}</div>}
    <div className="compact-metrics"><span><strong>{running}</strong> Running</span><span><strong>{queued}</strong> Queued</span><span className={failed ? 'danger-text' : ''}><strong>{failed}</strong> Failed</span></div>
    <Panel className="table-panel">
      <div className="table-toolbar"><div className="filter-pills"><button className={`filter-pill ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>All</button><button className={`filter-pill ${filter === 'active' ? 'active' : ''}`} onClick={() => setFilter('active')}>Active</button><button className={`filter-pill ${filter === 'history' ? 'active' : ''}`} onClick={() => setFilter('history')}>History</button></div></div>
      <div className="activity-list">
        {visibleJobs.map(job => <div className="activity-row" key={job.id}>
          <div className={`activity-glyph activity-${job.status}`}><ActivityIcon size={18} /></div>
          <div className="activity-primary"><div><strong>{job.runName}</strong><span className="job-kind">{job.kind}</span></div><span>{job.detail}</span>{job.status === 'running' && <ProgressBar value={job.progress} />}</div>
          <JobStatusBadge status={job.status} />
          <span className="activity-time">{formatRelativeDate(job.createdAt)}</span>
          {job.status === 'failed' && <button className="button button-ghost button-small" onClick={() => void retry(job.id)}><RotateCcw size={14} /> Retry</button>}
        </div>)}
      </div>
    </Panel>
  </>
}
