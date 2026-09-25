import { Ban, ChevronDown, ChevronUp, Database, FlaskConical, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useResource } from '../api/useResource'
import { formatRelativeDate, JobStatusBadge, ProgressBar } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader, Panel } from '../components/Page'
import type { ProcessingBatch } from '../types'

export function Processing() {
  const auth = useAuth()
  const canManage = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  const batches = useResource(api.processingBatches, [])
  const jobs = useResource(api.jobs, [])
  const catalogJobs = jobs.data.filter(job => job.kind === 'extract_metadata').slice(0, 10)
  const refreshJobs = useRef(jobs.refresh)
  const refreshBatches = useRef(batches.refresh)
  refreshJobs.current = jobs.refresh
  refreshBatches.current = batches.refresh
  const hasActiveWork = jobs.data.some(job => ['queued', 'running'].includes(job.status))
    || batches.data.some(batch => ['queued', 'running'].includes(batch.state))
  const [expanded, setExpanded] = useState<string | null>(null)
  const detailResource = useResource<ProcessingBatch | null>(() => expanded ? api.processingBatch(expanded) : Promise.resolve(null), null, expanded)
  const detail = detailResource.data
  const refreshDetail = useRef(detailResource.refresh)
  refreshDetail.current = detailResource.refresh
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hasActiveWork) return
    const timer = window.setInterval(() => {
      refreshJobs.current()
      refreshBatches.current()
      refreshDetail.current()
    }, 2000)
    return () => window.clearInterval(timer)
  }, [hasActiveWork])

  const toggle = (id: string) => setExpanded(current => current === id ? null : id)
  const mutate = async (id: string, kind: 'retry' | 'cancel') => {
    if (working) return
    setWorking(true)
    setError(null)
    try {
      if (kind === 'retry') await api.retryProcessingBatch(id)
      else await api.cancelProcessingBatch(id)
      batches.refresh()
      detailResource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Could not ${kind} the batch`)
    } finally {
      setWorking(false)
    }
  }

  return <>
    <PageHeader title="Processing" description="Monitor spectrum catalogs, metadata extraction, and conversion batches." actions={<button className="button button-secondary" onClick={() => {
      batches.refresh()
      jobs.refresh()
      detailResource.refresh()
    }}><RotateCw size={15} /> Refresh</button>} />
    {batches.error && <ApiErrorBanner message={batches.error} onRetry={batches.refresh} />}
    {jobs.error && <ApiErrorBanner message={jobs.error} onRetry={jobs.refresh} />}
    {detailResource.error && <ApiErrorBanner message={detailResource.error} onRetry={detailResource.refresh} />}
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
        <div className="batch-actions">{(detail.failedCount > 0 || detail.cancelledCount > 0) && <button className="button button-secondary button-small" disabled={working || !canManage} onClick={() => void mutate(detail.id, 'retry')}><RotateCw size={14} /> {detail.failedCount > 0 ? detail.cancelledCount > 0 ? 'Retry failed and cancelled' : 'Retry failed' : 'Retry cancelled'}</button>}{detail.queuedCount > 0 && <button className="button button-secondary button-small" disabled={working || !canManage} onClick={() => void mutate(detail.id, 'cancel')}><Ban size={14} /> Cancel queued</button>}</div>
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
