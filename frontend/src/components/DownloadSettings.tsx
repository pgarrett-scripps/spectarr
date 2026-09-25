import { useState } from 'react'
import { remoteApi, type DownloadSettings as DownloadPreference } from '../api/remote'
import { useResource } from '../api/useResource'
import { ApiErrorBanner, LoadingState, Panel } from './Page'

export function DownloadSettings() {
  const resource = useResource(remoteApi.settings, null)
  if (!resource.data) return resource.error ? <ApiErrorBanner message={resource.error} onRetry={resource.refresh} /> : <LoadingState label="Loading download settings" />
  return <DownloadForm initial={resource.data} />
}

function DownloadForm({ initial }: { initial: DownloadPreference }) {
  const [settings, setSettings] = useState(initial)
  const [concurrency, setConcurrency] = useState(initial.concurrency)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const save = async (value: number | null) => {
    setBusy(true)
    setError(null)
    setNotice('')
    try {
      const saved = await remoteApi.saveSettings(value)
      setSettings(saved)
      setConcurrency(saved.concurrency)
      setNotice('Download settings saved. Active transfers will finish normally.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save download settings')
    } finally {
      setBusy(false)
    }
  }
  return <Panel title="PRIDE downloads" subtitle="Control how many files download at the same time across this server.">
    <form onSubmit={event => {
      event.preventDefault()
      void save(concurrency)
    }}>
      <div className="form-grid"><label><span>Concurrent downloads</span><select value={concurrency} disabled={busy || settings.restore_mode} onChange={event => setConcurrency(Number(event.target.value))}>
        {Array.from({ length: 8 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}
      </select></label></div>
      <p className="repository-copy">Changes apply to new transfers without restarting. Lower limits take effect as active transfers finish. More downloads share the available bandwidth and disk space.</p>
      <p className="repository-copy">Server default: {settings.default_concurrency}. {settings.overridden ? 'A saved preference is in use.' : 'Using the server default.'}</p>
      {!settings.enabled && <p className="repository-copy">Online downloads are disabled by the server configuration.</p>}
      {settings.restore_mode && <p className="repository-copy">Settings are read-only during restore verification.</p>}
      {error && <p className="message-banner" role="alert">{error}</p>}
      {notice && <p className="repository-copy" role="status">{notice}</p>}
      <div className="import-actions">
        <button className="button button-secondary" type="button" disabled={busy || settings.restore_mode || !settings.overridden} onClick={() => void save(null)}>Use server default</button>
        <button className="button button-primary" type="submit" disabled={busy || settings.restore_mode}>{busy ? 'Saving' : 'Save download settings'}</button>
      </div>
    </form>
  </Panel>
}
