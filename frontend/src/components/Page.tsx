import { AlertCircle, LoaderCircle, Plus, RotateCw } from 'lucide-react'
import type { ReactNode } from 'react'

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string, title: string, description: string, actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  )
}

export function ApiErrorBanner({ message, onRetry }: { message: string, onRetry?: () => void }) {
  return (
    <div className="message-banner" role="alert">
      <div><AlertCircle size={17} /><span><strong>Live data unavailable</strong> {message}</span></div>
      {onRetry && <button className="button button-ghost button-small" onClick={onRetry}><RotateCw size={14} /> Retry</button>}
    </div>
  )
}

export function Panel({ title, subtitle, actions, children, className = '' }: { title?: string, subtitle?: string, actions?: ReactNode, children: ReactNode, className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel-header">
          <div>{title && <h2>{title}</h2>}{subtitle && <p>{subtitle}</p>}</div>
          {actions && <div>{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

export function EmptyState({ title, description, action = 'Add data', onAction }: { title: string, description: string, action?: string, onAction?: () => void }) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Plus size={22} /></div>
      <h3>{title}</h3>
      <p>{description}</p>
      {onAction && <button className="button button-primary" onClick={onAction}><Plus size={16} />{action}</button>}
    </div>
  )
}

export function LoadingState({ label = 'Loading live data' }: { label?: string }) {
  return <div className="content-loading" role="status"><LoaderCircle className="spin" size={20} /><span>{label}</span></div>
}
