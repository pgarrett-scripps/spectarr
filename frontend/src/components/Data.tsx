/* eslint-disable react-refresh/only-export-components */
import { CheckCircle2, CircleAlert, Clock3, LoaderCircle, XCircle } from 'lucide-react'
import type { Job, RunStatus } from '../types'

export function formatBytes(value: number): string {
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1000)), units.length - 1)
  const formatted = (value / 1000 ** exponent).toFixed(exponent > 2 ? 2 : 1).replace(/\.0+$|(?<=\.[0-9])0+$/, '')
  return `${formatted} ${units[exponent]}`
}

export function formatRelativeDate(date: string): string {
  const delta = Date.now() - new Date(date).getTime()
  const minutes = Math.round(delta / 60_000)
  if (minutes < 60) return `${Math.max(1, minutes)}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const map = {
    ready: { icon: CheckCircle2, label: 'Ready' },
    processing: { icon: LoaderCircle, label: 'Processing' },
    warning: { icon: CircleAlert, label: 'Limited source' },
    failed: { icon: XCircle, label: 'Failed' }
  }
  const item = map[status]
  return <span className={`status-badge status-${status}`}><item.icon size={14} />{item.label}</span>
}

export function JobStatusBadge({ status }: { status: Job['status'] }) {
  const map = {
    queued: { icon: Clock3, label: 'Queued' },
    running: { icon: LoaderCircle, label: 'Running' },
    complete: { icon: CheckCircle2, label: 'Complete' },
    failed: { icon: XCircle, label: 'Failed' }
  }
  const item = map[status]
  return <span className={`status-badge status-${status}`}><item.icon size={14} />{item.label}</span>
}

export function ProgressBar({ value, tone = 'blue' }: { value: number, tone?: 'blue' | 'green' | 'amber' }) {
  return <div className="progress-track" aria-label={`${value}%`}><div className={`progress-fill progress-${tone}`} style={{ width: `${Math.min(100, value)}%` }} /></div>
}

export function LoadingRows() {
  return <div className="loading-rows"><i /><i /><i /></div>
}
