import { AlertTriangle, CheckCircle2, Database, HardDrive, Server, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatBytes, ProgressBar } from '../components/Data'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'
import type { ConversionFormat, StorageReclaimPreview } from '../types'

export function Storage() {
  const resource = useResource(api.storage, [])
  const auth = useAuth()
  const [reclaiming, setReclaiming] = useState(false)
  const canReclaim = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  return <>
    <PageHeader title="Storage" description="Inspect the filesystem storage configured for this Spectarr instance." actions={canReclaim ? <button className="button button-danger" onClick={() => setReclaiming(true)}><Trash2 size={15} /> Clear generated files</button> : undefined} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <div className="storage-card-grid">
      {resource.data.map(location => {
        const pct = Math.round(location.usedBytes / location.capacityBytes * 100)
        return <article className="storage-card" key={location.id}>
          <div className="storage-card-head"><div className="storage-large-icon">{location.kind === 's3' ? <Database /> : <HardDrive />}</div><span className={`health health-${location.status}`}><i />{location.status}</span></div>
          <h2>{location.name}</h2><code>{location.path}</code>
          <div className="capacity-label"><strong>{pct}% used</strong><span>{formatBytes(location.usedBytes)} / {formatBytes(location.capacityBytes)}</span></div>
          <ProgressBar value={pct} tone={pct > 75 ? 'amber' : 'blue'} />
          <div className="storage-card-foot"><span>{location.artifactCount.toLocaleString()} artifacts</span><span>Configured by the server</span></div>
        </article>
      })}
    </div>
    <Panel title="Storage policy" subtitle="How Spectarr places and protects files">
      <div className="policy-grid">
        <Policy icon={<CheckCircle2 />} title="Search-ready directories" text="Every project has flat RAW, mzML, MGF, MS2, and other format folders." />
        <Policy icon={<Server />} title="Immutable originals" text="Readable files link to checksummed internal objects and are never modified in place." />
        <Policy icon={<Database />} title="Repairable view" text="Run manifests and the complete filesystem view can be reconstructed from Spectarr metadata." />
      </div>
    </Panel>
    {reclaiming && <ReclaimStorageDialog onClose={() => setReclaiming(false)} onReclaimed={resource.refresh} />}
  </>
}

function Policy({ icon, title, text }: { icon: React.ReactNode, title: string, text: string }) {
  return <div className="policy"><div>{icon}</div><span><strong>{title}</strong><small>{text}</small></span></div>
}

function ReclaimStorageDialog({ onClose, onReclaimed }: { onClose: () => void, onReclaimed: () => void }) {
  const projects = useResource(api.projects, [])
  const [projectId, setProjectId] = useState('')
  const [formats, setFormats] = useState<Set<ConversionFormat>>(new Set(['mzML', 'MGF']))
  const [preview, setPreview] = useState<StorageReclaimPreview | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [working, setWorking] = useState(false)
  const [complete, setComplete] = useState<StorageReclaimPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toggle = (format: ConversionFormat) => setFormats(current => {
    const next = new Set(current)
    if (next.has(format)) next.delete(format)
    else next.add(format)
    setPreview(null)
    setComplete(null)
    setConfirmation('')
    return next
  })
  const inspect = () => {
    if (!projectId || formats.size === 0) return
    setWorking(true)
    setError(null)
    void api.previewStorageReclaim({ projectId, formats: [...formats] }).then(setPreview).catch(reason => {
      setError(reason instanceof Error ? reason.message : 'Could not calculate reclaimable storage')
    }).finally(() => setWorking(false))
  }
  const reclaim = () => {
    if (!preview || confirmation !== 'PURGE DERIVED FILES') return
    setWorking(true)
    setError(null)
    void api.reclaimStorage({ projectId, formats: [...formats] }).then(result => {
      setComplete(result)
      setPreview(null)
      onReclaimed()
    }).catch(reason => {
      setError(reason instanceof Error ? reason.message : 'Could not clear generated files')
    }).finally(() => setWorking(false))
  }
  return <div className="modal-backdrop" role="presentation"><section className="modal-card reclaim-modal" role="dialog" aria-modal="true" aria-labelledby="reclaim-title">
    <div className="modal-header"><div><h2 id="reclaim-title">Clear generated files</h2><p>Remove regenerable derivatives while preserving sources, profiles, jobs, checksums, and provenance.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}>×</button></div>
    {complete ? <div className="process-result"><CheckCircle2 size={30} /><h3>Generated files cleared</h3><p>{complete.artifactCount} files marked as purged, representing {formatBytes(complete.reclaimableBytes)} of logical data. They can be generated again from their source files.</p><button className="button button-primary" onClick={onClose}>Done</button></div> : <div className="modal-fields">
      <label><span>Project</span><select value={projectId} onChange={event => {
        setProjectId(event.target.value)
        setPreview(null)
        setConfirmation('')
      }}><option value="">Select a project</option>{projects.data.filter(project => !project.systemKey).map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
      <fieldset className="choice-fieldset"><legend>Generated formats</legend>{(['mzML', 'MGF', 'MS2', 'mzXML'] as ConversionFormat[]).map(format => <label className="check-field" key={format}><input type="checkbox" checked={formats.has(format)} onChange={() => toggle(format)} /><span>{format}</span></label>)}</fieldset>
      <button className="button button-secondary" disabled={working || !projectId || formats.size === 0} onClick={inspect}>{working ? 'Calculating' : 'Calculate reclaimable storage'}</button>
      {preview && <div className="reclaim-preview"><AlertTriangle size={21} /><div><strong>{preview.artifactCount} generated files</strong><span>{formatBytes(preview.reclaimableBytes)} of logical data can be cleared.{preview.blockedCount ? ` ${preview.blockedCount} active files will be skipped.` : ''}</span></div></div>}
      {preview && preview.artifactCount > 0 && <label><span>Type <strong>PURGE DERIVED FILES</strong> to confirm</span><input value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>}
      {error && <div className="modal-error" role="alert">{error}</div>}
      <div className="modal-actions"><button className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-danger" disabled={working || !preview || preview.artifactCount === 0 || confirmation !== 'PURGE DERIVED FILES'} onClick={reclaim}>{working ? 'Clearing' : 'Clear generated files'}</button></div>
    </div>}
  </section></div>
}
