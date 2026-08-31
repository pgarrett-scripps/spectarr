import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'

interface MoveRunsDialogProps {
  runIds: string[]
  onClose: () => void
  onMoved: () => void
}

export function MoveRunsDialog({ runIds, onClose, onMoved }: MoveRunsDialogProps) {
  const projects = useResource(api.projects, [])
  const experiments = useResource(() => api.experiments(), [])
  const destinationProjects = useMemo(
    () => projects.data.filter(project => !project.systemKey),
    [projects.data]
  )
  const [projectId, setProjectId] = useState('')
  const [experimentId, setExperimentId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selectedProjectId = projectId || destinationProjects[0]?.id || ''
  const projectExperiments = experiments.data.filter(experiment => experiment.projectId === selectedProjectId)
  const selectedExperimentId = experimentId && projectExperiments.some(item => item.id === experimentId)
    ? experimentId
    : projectExperiments[0]?.id || ''

  const move = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedExperimentId) {
      setError('Choose a destination experiment')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.assignRuns(runIds, selectedExperimentId)
      onMoved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not move the selected runs')
    } finally {
      setSubmitting(false)
    }
  }

  return <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="move-runs-title">
    <div className="modal-header"><div><h2 id="move-runs-title">Move {runIds.length === 1 ? 'run' : `${runIds.length} runs`}</h2><p>Artifacts and processing history remain attached.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}>×</button></div>
    {destinationProjects.length === 0 ? <div className="token-result"><strong>No destination projects</strong><p>Create a scientific project and experiment before assigning inbox runs.</p><Link className="button button-primary" to="/projects" onClick={onClose}>Open Projects</Link></div> : <form onSubmit={event => void move(event)}>
      <div className="modal-fields">
        <label><span>Project</span><select aria-label="Destination project" value={selectedProjectId} onChange={event => {
          setProjectId(event.target.value)
          setExperimentId('')
        }}>{destinationProjects.map(project => <option value={project.id} key={project.id}>{project.name}</option>)}</select></label>
        <label><span>Experiment</span><select aria-label="Destination experiment" value={selectedExperimentId} onChange={event => setExperimentId(event.target.value)} required>{projectExperiments.length === 0 && <option value="">No experiments in this project</option>}{projectExperiments.map(experiment => <option value={experiment.id} key={experiment.id}>{experiment.name}</option>)}</select></label>
      </div>
      {(error || projects.error || experiments.error) && <div className="modal-error" role="alert">{error ?? projects.error ?? experiments.error}</div>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting || !selectedExperimentId}>{submitting ? 'Moving' : 'Move runs'}</button></div>
    </form>}
  </section></div>
}
