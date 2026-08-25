import { CheckCircle2, FlaskConical, LoaderCircle } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import type { ProcessingBatch, ProcessingBatchPreview } from '../types'
import { ProgressBar } from './Data'

export function ProcessDialog({ projectId, runIds, onClose }: {
  projectId?: string
  runIds?: string[]
  onClose: () => void
}) {
  const profiles = useResource(api.processingProfiles, [])
  const experiments = useResource(() => projectId ? api.experiments(projectId) : Promise.resolve([]), [])
  const [scopeChoice, setScopeChoice] = useState<'project' | 'experiments'>(projectId ? 'project' : 'experiments')
  const [experimentIds, setExperimentIds] = useState<Set<string>>(new Set())
  const [profileIds, setProfileIds] = useState<Set<string>>(new Set())
  const [mode, setMode] = useState<ProcessingBatch['mode']>('missing')
  const [preview, setPreview] = useState<ProcessingBatchPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [queued, setQueued] = useState<ProcessingBatch | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const enabledProfiles = profiles.data.filter(profile => profile.enabled)

  useEffect(() => {
    if (profileIds.size || !enabledProfiles.length) return
    const standard = enabledProfiles.find(profile => profile.name === 'Standard mzML') ?? enabledProfiles[0]
    setProfileIds(new Set([standard.id]))
  }, [enabledProfiles, profileIds.size])

  const scope = useMemo(() => {
    if (runIds?.length) return { scopeType: 'runs' as const, scopeIds: runIds }
    if (scopeChoice === 'experiments') return { scopeType: 'experiments' as const, scopeIds: [...experimentIds] }
    return { scopeType: 'project' as const, scopeIds: projectId ? [projectId] : [] }
  }, [experimentIds, projectId, runIds, scopeChoice])
  const recipeIds = useMemo(() => [...profileIds], [profileIds])

  useEffect(() => {
    if (!scope.scopeIds.length || !recipeIds.length || queued) {
      setPreview(null)
      return
    }
    let cancelled = false
    setPreviewing(true)
    setError(null)
    void api.previewProcessingBatch({ ...scope, recipeIds, mode }).then(value => {
      if (!cancelled) setPreview(value)
    }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not preview the batch')
    }).finally(() => {
      if (!cancelled) setPreviewing(false)
    })
    return () => {
      cancelled = true
    }
  }, [mode, queued, recipeIds, scope])

  const toggleProfile = (id: string) => setProfileIds(current => toggleSet(current, id))
  const toggleExperiment = (id: string) => setExperimentIds(current => toggleSet(current, id))
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!scope.scopeIds.length || !recipeIds.length) return
    setSubmitting(true)
    setError(null)
    try {
      setQueued(await api.createProcessingBatch({ ...scope, recipeIds, mode }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue the processing batch')
    } finally {
      setSubmitting(false)
    }
  }

  return <div className="modal-backdrop" role="presentation"><section className="modal-card process-modal" role="dialog" aria-modal="true" aria-labelledby="process-title">
    <div className="modal-header"><div><h2 id="process-title">Process mass spectrometry files</h2><p>Generate reproducible derivatives for a collection of runs.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}>×</button></div>
    {queued ? <div className="process-result"><CheckCircle2 size={30} /><h3>Batch queued</h3><p>{queued.queuedCount} jobs queued, {queued.skippedCount} targets skipped.</p><ProgressBar value={queued.progress} /><div className="modal-actions"><button className="button button-secondary" onClick={onClose}>Close</button><Link className="button button-primary" to="/processing" onClick={onClose}>Open processing</Link></div></div> : <form onSubmit={event => void submit(event)}>
      <div className="modal-fields">
        {projectId && !runIds?.length && <label><span>Scope</span><select aria-label="Processing scope" value={scopeChoice} onChange={event => setScopeChoice(event.target.value as typeof scopeChoice)}><option value="project">Entire project</option><option value="experiments">Selected experiments</option></select></label>}
        {scopeChoice === 'experiments' && !runIds?.length && <fieldset className="choice-fieldset"><legend>Experiments</legend>{experiments.data.length === 0 ? <span className="muted-cell">No experiments are available.</span> : experiments.data.map(experiment => <label className="check-field" key={experiment.id}><input type="checkbox" checked={experimentIds.has(experiment.id)} onChange={() => toggleExperiment(experiment.id)} /><span>{experiment.name}</span></label>)}</fieldset>}
        {runIds?.length && <div className="scope-summary"><strong>{runIds.length} selected {runIds.length === 1 ? 'run' : 'runs'}</strong><span>Every selected run with a ready source file is evaluated.</span></div>}
        <fieldset className="choice-fieldset"><legend>Processing profiles</legend>{profiles.loading ? <span className="muted-cell">Loading profiles</span> : enabledProfiles.map(profile => <label className="profile-choice" key={profile.id}><input type="checkbox" checked={profileIds.has(profile.id)} onChange={() => toggleProfile(profile.id)} /><span><strong>{profile.name}</strong><small>{profile.outputFormat} · revision {profile.revision}</small><em>{profile.description}</em></span></label>)}</fieldset>
        <label><span>Existing outputs</span><select aria-label="Existing output handling" value={mode} onChange={event => setMode(event.target.value as ProcessingBatch['mode'])}><option value="missing">Generate missing only</option><option value="missing_or_stale">Generate missing and outdated</option><option value="force">Regenerate everything</option></select></label>
        <div className="batch-preview" aria-live="polite">{previewing ? <span><LoaderCircle className="spin" size={16} /> Calculating</span> : preview ? <><span><strong>{preview.queueCount}</strong> to queue</span><span><strong>{preview.currentCount}</strong> current</span><span><strong>{preview.staleCount}</strong> outdated</span><span><strong>{preview.queuedCount}</strong> already queued</span><span><strong>{preview.incompatibleCount}</strong> not applicable</span></> : <span>Select a scope and at least one profile.</span>}</div>
      </div>
      {(error || profiles.error || experiments.error) && <div className="modal-error" role="alert">{error ?? profiles.error ?? experiments.error}</div>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting || previewing || !preview || preview.queueCount === 0 || !recipeIds.length || !scope.scopeIds.length}><FlaskConical size={15} />{submitting ? 'Queueing' : `Queue ${preview?.queueCount ?? 0} jobs`}</button></div>
    </form>}
  </section></div>
}

function toggleSet(current: Set<string>, id: string) {
  const next = new Set(current)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}
