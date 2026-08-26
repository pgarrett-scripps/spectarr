import { Ban, ChevronDown, ChevronUp, Database, FlaskConical, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatRelativeDate, JobStatusBadge, ProgressBar } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader, Panel } from '../components/Page'
import type { ProcessingBatch } from '../types'

export function Processing() {
  const batches = useResource(api.processingBatches, [])
  const jobs = useResource(api.jobs, [])
  const catalogJobs = jobs.data.filter(job => job.kind === 'extract_metadata').slice(0, 10)
  const refreshJobs = useRef(jobs.refresh)
  const refreshBatches = useRef(batches.refresh)
  refreshJobs.current = jobs.refresh
  refreshBatches.current = batches.refresh
  const hasActiveWork = catalogJobs.some(job => ['queued', 'running'].includes(job.status))
    || batches.data.some(batch => ['queued', 'running'].includes(batch.state))
  const [expanded, setExpanded] = useState<string | null>(null)
  const [detail, setDetail] = useState<ProcessingBatch | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hasActiveWork) return
    const timer = window.setInterval(() => {
      refreshJobs.current()
      refreshBatches.current()
    }, 2000)
    return () => window.clearInterval(timer)
  }, [hasActiveWork])

  const toggle = async (id: string) => {
    if (expanded === id) {
      setExpanded(null)
      setDetail(null)
      return
    }
    setError(null)
    try {
      setDetail(await api.processingBatch(id))
      setExpanded(id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load batch details')
    }
  }
  const retry = async (id: string) => {
    setError(null)
    try {
      setDetail(await api.retryProcessingBatch(id))
      batches.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not retry the batch')
    }
  }
  const cancel = async (id: string) => {
    setError(null)
    try {
      setDetail(await api.cancelProcessingBatch(id))
      batches.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not cancel the batch')
    }
  }

  return <>
    <PageHeader title="Processing" description="Monitor spectrum catalogs, metadata extraction, and conversion batches." actions={<button className="button button-secondary" onClick={() => {
      batches.refresh()
      jobs.refresh()
      if (expanded) void api.processingBatch(expanded).then(setDetail).catch(() => undefined)
    }}><RotateCw size={15} /> Refresh</button>} />
    {batches.error && <ApiErrorBanner message={batches.error} onRetry={batches.refresh} />}
    {jobs.error && <ApiErrorBanner message={jobs.error} onRetry={jobs.refresh} />}
    {error && <div className="message-banner" role="alert">{error}</div>}
    {catalogJobs.length > 0 && <Panel title="Spectrum catalogs" subtitle="Recent catalog and scientific metadata extraction jobs" className="table-panel"><div className="activity-list">{catalogJobs.map(job => <div className="activity-row" key={job.id}>
      <div className={`activity-glyph activity-${job.status}`}><Database size={18} /></div>
      <div className="activity-primary"><div><strong>{job.runName}</strong><span className="job-kind">spectrum catalog</span></div><span>{job.detail === 'extract_metadata' ? 'Build searchable spectrum catalog' : job.detail}</span>{job.status === 'running' && <ProgressBar value={job.progress} />}</div>
      <JobStatusBadge status={job.status} />
      <span className="activity-time">{formatRelativeDate(job.createdAt)}</span>
    </div>)}</div></Panel>}
    {batches.data.length > 0 && <Panel className="batch-list-panel"><div className="batch-list">{batches.data.map(batch => <article className="batch-row" key={batch.id}>
      <button className="batch-main" onClick={() => void toggle(batch.id)} aria-expanded={expanded === batch.id}>
        <span className={`activity-glyph activity-${batch.state === 'succeeded' ? 'complete' : batch.state}`}><FlaskConical size={18} /></span>
        <span className="batch-summary"><span><strong>{batch.label ?? scopeLabel(batch)}</strong><i className={`health health-${batch.state === 'succeeded' ? 'healthy' : batch.state === 'failed' ? 'failed' : 'warning'}`}>{batch.state}</i></span><small>{batch.totalCount} targets · {batch.succeededCount} complete · {batch.skippedCount} skipped · {formatRelativeDate(batch.createdAt)}</small><ProgressBar value={batch.progress} /></span>
        {expanded === batch.id ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
      </button>
      {expanded === batch.id && detail?.id === batch.id && <div className="batch-detail">
        <div className="batch-actions">{detail.failedCount > 0 && <button className="button button-secondary button-small" onClick={() => void retry(detail.id)}><RotateCw size={14} /> Retry failed</button>}{detail.queuedCount > 0 && <button className="button button-secondary button-small" onClick={() => void cancel(detail.id)}><Ban size={14} /> Cancel queued</button>}</div>
        <div className="table-scroll"><table><thead><tr><th>Run</th><th>Profile</th><th>Format</th><th>Status</th><th>Reason</th></tr></thead><tbody>{detail.items.map(item => <tr key={item.id}><td>{item.runName}</td><td>{item.recipeName}</td><td><span className="format-chip">{item.outputFormat}</span></td><td>{item.state}</td><td className="muted-cell">{item.error ?? item.reason}</td></tr>)}</tbody></table></div>
      </div>}
    </article>)}</div></Panel>}
    {!jobs.loading && !batches.loading && catalogJobs.length === 0 && batches.data.length === 0 && <EmptyState title="No processing jobs" description="Build a spectrum catalog or process runs to generate mzML, MGF, or another configured output." />}
  </>
}

function scopeLabel(batch: ProcessingBatch) {
  if (batch.scopeType === 'project') return 'Project processing'
  if (batch.scopeType === 'experiments') return `${batch.scopeIds.length} experiment batch`
  return `${batch.scopeIds.length} run batch`
}
