import { Ban, ChevronDown, ChevronUp, FlaskConical, RotateCw } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatRelativeDate, ProgressBar } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader, Panel } from '../components/Page'
import type { ProcessingBatch } from '../types'

export function Processing() {
  const batches = useResource(api.processingBatches, [])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [detail, setDetail] = useState<ProcessingBatch | null>(null)
  const [error, setError] = useState<string | null>(null)

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
    <PageHeader title="Processing" description="Monitor project, experiment, and selected-run conversion batches." actions={<button className="button button-secondary" onClick={() => {
      batches.refresh()
      if (expanded) void api.processingBatch(expanded).then(setDetail).catch(() => undefined)
    }}><RotateCw size={15} /> Refresh</button>} />
    {batches.error && <ApiErrorBanner message={batches.error} onRetry={batches.refresh} />}
    {error && <div className="message-banner" role="alert">{error}</div>}
    {batches.data.length === 0 ? <EmptyState title="No processing batches" description="Process a project or select runs to generate mzML, MGF, or another configured output." /> : <Panel className="batch-list-panel"><div className="batch-list">{batches.data.map(batch => <article className="batch-row" key={batch.id}>
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
  </>
}

function scopeLabel(batch: ProcessingBatch) {
  if (batch.scopeType === 'project') return 'Project processing'
  if (batch.scopeType === 'experiments') return `${batch.scopeIds.length} experiment batch`
  return `${batch.scopeIds.length} run batch`
}
