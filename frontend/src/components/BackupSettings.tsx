import { DatabaseBackup, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { formatBytes } from './Data'
import { ApiErrorBanner, LoadingState, Panel } from './Page'
import type { BackupPolicy } from '../types'

const timestamp = (value: string | null) => value ? new Date(value).toLocaleString() : 'None yet'
const activeStates = new Set(['queued', 'creating', 'verifying', 'restoring'])

export function BackupSettings() {
  const resource = useResource(api.backups, null)
  const refresh = useRef(resource.refresh)
  refresh.current = resource.refresh
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const timer = window.setInterval(() => refresh.current(), 5000)
    return () => window.clearInterval(timer)
  }, [])
  const act = async (action: () => Promise<unknown>) => {
    setWorking(true)
    setError(null)
    try {
      await action()
      resource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Backup operation failed')
    } finally {
      setWorking(false)
    }
  }
  const data = resource.data
  if (!data) return resource.error ? <ApiErrorBanner message={resource.error} onRetry={resource.refresh} /> : <LoadingState label="Loading backup settings" />
  const busy = working || activeStates.has(data.status)
  const canRun = data.destination_available && !busy && !data.restore_mode
  return <>
    {error && <div className="message-banner" role="alert">{error}</div>}
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <Panel title="Backup status" subtitle="Verified copies of the database, acquisitions, and runtime credentials">
      <div className="backup-status" aria-live="polite">
        <strong>{activeStates.has(data.status) ? `${data.status[0].toUpperCase()}${data.status.slice(1)}` : data.status === 'failed' || data.status === 'interrupted' ? 'Backup needs attention' : data.last_success_at ? 'Last backup verified' : 'No verified backup yet'}</strong>
        <dl>
          <dt>Last verified backup</dt><dd>{timestamp(data.last_success_at)}</dd>
          <dt>Last restore check</dt><dd>{timestamp(data.last_restore_at)}</dd>
          <dt>Next scheduled backup</dt><dd>{data.next_backup_at ? timestamp(data.next_backup_at) : 'Schedule disabled'}</dd>
          <dt>Destination</dt><dd>{data.destination ?? 'Not configured'}</dd>
          <dt>Available space</dt><dd>{data.free_bytes === null ? 'Unavailable' : formatBytes(data.free_bytes)}</dd>
        </dl>
        {data.last_error && <p className="message-banner" role="alert">{data.last_error}</p>}
        {data.last_restore_error && data.last_restore_error !== data.last_error && <p className="message-banner" role="alert">Restore check: {data.last_restore_error}</p>}
        {data.destination_error && <p className="message-banner" role="alert">{data.destination_error}</p>}
        {data.same_filesystem && <p>The destination shares a filesystem with live data. Use a separate disk or mounted backup system for protection from disk failure.</p>}
        {data.restore_mode && <p>Backup operations are disabled while this instance is in restore verification mode.</p>}
        {!data.configured && <p>Mount an existing backup directory with the supplied Compose backup override, then return here to enable scheduling.</p>}
      </div>
      <div className="import-actions">
        <button className="button button-secondary" disabled={!canRun || data.history.length === 0} onClick={() => void act(api.checkBackupRestore)}><RotateCw size={15} /> Test latest restore</button>
        <button className="button button-primary" disabled={!canRun} onClick={() => void act(api.runBackup)}><DatabaseBackup size={15} /> Back up now</button>
      </div>
    </Panel>
    <PolicyForm key={JSON.stringify(data.policy)} initial={data.policy} busy={busy || data.restore_mode} available={data.destination_available} save={policy => act(() => api.saveBackupPolicy(policy))} />
    <Panel title="Verified backups" subtitle="Retention applies only after a new backup and any due restore check succeed">
      {data.history.length === 0 ? <p className="backup-status">No verified snapshots are available at this destination.</p> : <div className="table-scroll"><table className="backup-history">
        <thead><tr><th>Created</th><th>Size</th><th>Artifact objects</th><th>Snapshot folder</th></tr></thead>
        <tbody>{data.history.map(snapshot => <tr key={snapshot.id}><td>{timestamp(snapshot.created_at)}</td><td>{formatBytes(snapshot.byte_size)}</td><td>{snapshot.artifact_objects}</td><td><code>{snapshot.id}</code></td></tr>)}</tbody>
      </table></div>}
      {data.history.length > 0 && <p className="backup-status">Folders are under <code>{data.destination}/spectarr-{data.instance_id}</code>. Use the included verification and restore scripts for recovery.</p>}
    </Panel>
  </>
}

function PolicyForm({ initial, busy, available, save }: { initial: BackupPolicy, busy: boolean, available: boolean, save: (policy: BackupPolicy) => Promise<void> }) {
  const [policy, setPolicy] = useState(initial)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void save(policy)
  }
  return <Panel title="Backup schedule" subtitle="Full snapshots pause application writes while the database and files are copied">
    <form onSubmit={submit}>
      <div className="form-grid">
        <label><span>Automatic backups</span><select value={String(policy.enabled)} disabled={busy} onChange={event => setPolicy({ ...policy, enabled: event.target.value === 'true' })}><option value="false">Disabled</option><option value="true" disabled={!available}>Enabled</option></select></label>
        <label><span>Backup time (UTC)</span><input type="time" required value={policy.time_utc} disabled={busy} onChange={event => setPolicy({ ...policy, time_utc: event.target.value })} /></label>
        <label><span>Back up every (days)</span><input type="number" required min={1} max={365} value={policy.every_days} disabled={busy} onChange={event => setPolicy({ ...policy, every_days: Number(event.target.value) })} /></label>
        <label><span>Keep newest backups</span><input type="number" required min={1} max={1000} value={policy.keep_last} disabled={busy} onChange={event => setPolicy({ ...policy, keep_last: Number(event.target.value) })} /></label>
        <label><span>Restore check interval (days)</span><input type="number" required min={1} max={365} value={policy.restore_every_days} disabled={busy} onChange={event => setPolicy({ ...policy, restore_every_days: Number(event.target.value) })} /></label>
      </div>
      <p className="backup-status">Every snapshot is verified. The first snapshot also receives a restore check, followed by checks at this interval when a backup runs. Restore checks need space for one additional copy. Schedule backups during a quiet period.</p>
      <div className="import-actions"><button type="submit" className="button button-primary" disabled={busy || (policy.enabled && !available)}>Save backup policy</button></div>
    </form>
  </Panel>
}
