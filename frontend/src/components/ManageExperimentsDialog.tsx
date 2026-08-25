import { AlertTriangle, LoaderCircle, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import type { Experiment, ExperimentDeletionPreview } from '../types'
import { formatBytes } from './Data'

export function ManageExperimentsDialog({ projectId, onClose, onDeleted }: {
  projectId: string
  onClose: () => void
  onDeleted: () => void
}) {
  const experiments = useResource(() => api.experiments(projectId), [])
  const [selected, setSelected] = useState<Experiment | null>(null)
  const [preview, setPreview] = useState<ExperimentDeletionPreview | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const choose = (experiment: Experiment) => {
    setSelected(experiment)
    setPreview(null)
    setConfirmation('')
    setError(null)
    setLoading(true)
    void api.experimentDeletionPreview(experiment.id).then(setPreview).catch(reason => {
      setError(reason instanceof Error ? reason.message : 'Could not inspect the experiment')
    }).finally(() => setLoading(false))
  }

  const remove = () => {
    if (!selected || confirmation !== selected.name) return
    setLoading(true)
    setError(null)
    void api.deleteExperiment(selected.id, confirmation).then(() => {
      setSelected(null)
      setPreview(null)
      setConfirmation('')
      experiments.refresh()
      onDeleted()
    }).catch(reason => {
      setError(reason instanceof Error ? reason.message : 'Could not delete the experiment')
    }).finally(() => setLoading(false))
  }

  return <div className="modal-backdrop" role="presentation"><section className="modal-card manage-experiments-modal" role="dialog" aria-modal="true" aria-labelledby="manage-experiments-title">
    <div className="modal-header"><div><h2 id="manage-experiments-title">Manage experiments</h2><p>Delete an experiment and all source and generated files it owns.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}>×</button></div>
    {!selected ? <div className="experiment-management-list">
      {experiments.loading ? <span className="muted-cell">Loading experiments</span> : experiments.data.map(experiment => <div key={experiment.id}>
        <span><strong>{experiment.name}</strong><small>{experiment.intakeAgentId ? 'Managed instrument inbox' : experiment.description || 'No description'}</small></span>
        <button className="button button-danger button-small" disabled={Boolean(experiment.intakeAgentId)} onClick={() => choose(experiment)}><Trash2 size={13} /> Delete</button>
      </div>)}
      {experiments.data.length === 0 && !experiments.loading && <span className="muted-cell">This project has no experiments.</span>}
    </div> : <div className="destructive-confirmation">
      <AlertTriangle size={28} />
      <h3>Delete {selected.name}?</h3>
      {loading && !preview ? <span><LoaderCircle className="spin" size={15} /> Inspecting experiment</span> : preview && <p>This permanently removes {preview.runCount} runs, {preview.sourceCount} source files, {preview.derivedCount} generated files, and {formatBytes(preview.logicalBytes)} of logical data.</p>}
      <label><span>Type <strong>{selected.name}</strong> to confirm</span><input value={confirmation} onChange={event => setConfirmation(event.target.value)} autoFocus /></label>
      {error && <div className="modal-error" role="alert">{error}</div>}
      <div className="modal-actions"><button className="button button-secondary" onClick={() => setSelected(null)}>Back</button><button className="button button-danger" disabled={loading || !preview || confirmation !== selected.name} onClick={remove}>{loading ? 'Deleting' : 'Delete experiment'}</button></div>
    </div>}
    {!selected && (error || experiments.error) && <div className="modal-error" role="alert">{error ?? experiments.error}</div>}
    {!selected && <div className="modal-actions"><button className="button button-secondary" onClick={onClose}>Close</button></div>}
  </section></div>
}
