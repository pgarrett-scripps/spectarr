import { BookOpen, Braces, Check, Clipboard, Plus, Radio, Trash2, Webhook } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatRelativeDate } from '../components/Data'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'

export function Integrations() {
  const [copied, setCopied] = useState<'api' | 'mcp' | 'webhook' | null>(null)
  const [creatingWebhook, setCreatingWebhook] = useState(false)
  const [webhookSecret, setWebhookSecret] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const auth = useAuth()
  const health = useResource(api.systemHealth, null)
  const webhooks = useResource(api.webhooks, [])
  const deliveries = useResource(api.webhookDeliveries, [])
  const copy = async (kind: 'api' | 'mcp' | 'webhook', value: string) => {
    setError(null)
    try {
      await navigator.clipboard.writeText(value)
      setCopied(kind)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not copy to the clipboard')
    }
  }
  const createWebhook = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError(null)
    try {
      const webhook = await api.createWebhook({
        name: String(form.get('name')),
        url: String(form.get('url')),
        eventFilters: form.getAll('events').map(String)
      })
      setWebhookSecret(webhook.signingSecret ?? null)
      webhooks.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the webhook')
    }
  }
  return <>
    <PageHeader title="API & MCP" description="Connect scripts, agents, and downstream search applications." />
    <div className="integration-grid">
      <Panel title="REST API" subtitle="Versioned access to library data and operations">
        <div className="integration-status"><span className="integration-icon"><Braces size={23} /></span><div><strong>Spectarr API</strong><span>{health.data ? `Version ${health.data.version}` : 'Health unavailable'}</span></div><span className={`health health-${health.data?.status === 'ok' ? 'healthy' : 'offline'}`}><i />{health.data?.status ?? 'offline'}</span></div>
        <div className="endpoint"><code>http://localhost:8280/api/v1</code><button className="icon-button" aria-label="Copy API URL" onClick={() => void copy('api', 'http://localhost:8280/api/v1')}>{copied === 'api' ? <Check size={15} /> : <Clipboard size={15} />}</button></div>
        <div className="integration-links"><a className="button button-secondary" href="http://localhost:8280/docs"><BookOpen size={15} /> API docs</a><a className="button button-secondary" href="http://localhost:8280/openapi.json">OpenAPI JSON</a></div>
      </Panel>
      <Panel title="MCP server" subtitle="Structured tools and resources for AI clients">
        <div className="integration-status"><span className="integration-icon integration-icon-cyan"><Radio size={23} /></span><div><strong>Streamable HTTP endpoint</strong><span>Read tools enabled by default</span></div></div>
        <div className="endpoint"><code>http://localhost:8281/mcp</code><button className="icon-button" aria-label="Copy MCP URL" onClick={() => void copy('mcp', 'http://localhost:8281/mcp')}>{copied === 'mcp' ? <Check size={15} /> : <Clipboard size={15} />}</button></div>
        <div className="scope-list"><span><Check size={13} /> Search runs</span><span><Check size={13} /> Read metadata</span><span><Check size={13} /> Inspect artifacts</span></div>
      </Panel>
    </div>
    {health.error && <ApiErrorBanner message={`API health: ${health.error}`} onRetry={health.refresh} />}
    {webhooks.error && <ApiErrorBanner message={`Webhooks: ${webhooks.error}`} onRetry={webhooks.refresh} />}
    {deliveries.error && <ApiErrorBanner message={`Webhook deliveries: ${deliveries.error}`} onRetry={deliveries.refresh} />}
    {auth.user?.role === 'admin' && <Panel title="Webhooks" subtitle="Signed, retried delivery of artifact and run events" actions={<button className="button button-primary button-small" onClick={() => {
      setWebhookSecret(null)
      setCopied(null)
      setError(null)
      setCreatingWebhook(true)
    }}><Plus size={14} /> Add webhook</button>}>
      {webhooks.data.length === 0 ? <div className="webhook-empty"><Webhook size={22} /><div><strong>No webhooks configured</strong><span>Add a destination for events such as artifact.ready and artifact.metadata_extracted.</span></div></div> : <div className="webhook-list">{webhooks.data.map(webhook => <div key={webhook.id}><span className="integration-icon integration-icon-cyan"><Webhook size={17} /></span><div><strong>{webhook.name}</strong><small>{webhook.url}</small><span>{webhook.eventFilters.join(', ') || 'All events'}</span></div><span className={`health health-${webhook.enabled ? 'healthy' : 'offline'}`}><i />{webhook.enabled ? 'enabled' : 'disabled'}</span><button className="button button-ghost button-small" onClick={() => void api.updateWebhook(webhook.id, !webhook.enabled).then(webhooks.refresh).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not update webhook'))}>{webhook.enabled ? 'Disable' : 'Enable'}</button><button className="icon-button" aria-label={`Delete ${webhook.name}`} onClick={() => void api.deleteWebhook(webhook.id).then(webhooks.refresh).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not delete webhook'))}><Trash2 size={14} /></button></div>)}</div>}
      {deliveries.data.length > 0 && <div className="delivery-summary"><strong>Recent deliveries</strong>{deliveries.data.slice(0, 5).map(delivery => <span key={delivery.id}>{delivery.status} · {delivery.responseStatus ?? 'no response'} · {formatRelativeDate(delivery.createdAt)}</span>)}</div>}
      {error && <div className="modal-error setting-error" role="alert">{error}</div>}
    </Panel>}
    {creatingWebhook && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="webhook-title">
      <div className="modal-header"><div><h2 id="webhook-title">Add webhook</h2><p>The signing secret is shown once.</p></div><button className="icon-button" aria-label="Close" onClick={() => setCreatingWebhook(false)}>×</button></div>
      {webhookSecret ? <div className="token-result"><strong>Signing secret</strong><p>Use this to verify the request signature at your destination.</p><div className="endpoint"><code>{webhookSecret}</code><button className="icon-button" aria-label="Copy signing secret" onClick={() => void copy('webhook', webhookSecret)}>{copied === 'webhook' ? <Check size={15} /> : <Clipboard size={15} />}</button></div>{error && <div className="modal-error" role="alert">{error}</div>}<button className="button button-primary" onClick={() => setCreatingWebhook(false)}>Done</button></div> : <form onSubmit={event => void createWebhook(event)}><div className="modal-fields"><label><span>Name</span><input name="name" required autoFocus /></label><label><span>HTTPS destination</span><input name="url" type="url" required placeholder="https://searcharr.example/hooks/spectarr" /></label><label className="check-field"><input type="checkbox" name="events" value="artifact.ready" defaultChecked /><span>Artifact ready</span></label><label className="check-field"><input type="checkbox" name="events" value="artifact.metadata_extracted" defaultChecked /><span>Metadata extracted</span></label></div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setCreatingWebhook(false)}>Cancel</button><button type="submit" className="button button-primary">Create webhook</button></div></form>}
    </section></div>}
  </>
}
