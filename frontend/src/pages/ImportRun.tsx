import { ArrowLeft, Upload } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { LoadingState, PageHeader, Panel } from '../components/Page'
import { projectRunsPath } from '../navigation'
import type { Project } from '../types'

export function ImportRun() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project') ?? ''
  const project = useResource<Project | null>(() => projectId ? api.project(projectId) : Promise.resolve(null), null, projectId)
  const experiments = useResource(() => projectId ? api.experiments(projectId) : Promise.resolve([]), [], projectId)
  const [sourceMode, setSourceMode] = useState<'file' | 'path'>('file')
  const [projectName, setProjectName] = useState('')
  const [experimentName, setExperimentName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [sourcePath, setSourcePath] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const backPath = projectId ? projectRunsPath(projectId) : '/projects'

  useEffect(() => {
    if (project.data) setProjectName(project.data.name)
  }, [project.data])

  useEffect(() => {
    if (!experimentName && experiments.data[0]) setExperimentName(experiments.data[0].name)
  }, [experimentName, experiments.data])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError(null)
    setSubmitting(true)
    try {
      const run = await api.importRun({
        projectName: String(form.get('projectName')),
        experimentName: String(form.get('experimentName')),
        sampleName: String(form.get('sampleName')),
        runName: String(form.get('runName')),
        file: sourceMode === 'file' ? file ?? undefined : undefined,
        sourcePath: sourceMode === 'path' ? sourcePath : undefined
      })
      navigate(`/projects/${run.projectId}/runs/${run.id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Import failed')
    } finally {
      setSubmitting(false)
    }
  }

  return <>
    <Link className="back-link" to={backPath}><ArrowLeft size={15} /> {project.data?.name ?? 'Projects'}</Link>
    <PageHeader title="Import mass spectrometry data" description={project.data ? `Add a new run to ${project.data.name}.` : 'Create a run from vendor data, mzML, mzXML, MGF, or MS2.'} />
    {(error || project.error || experiments.error) && <div className="message-banner" role="alert">{error ?? project.error ?? experiments.error}</div>}
    {(project.loading || experiments.loading) && projectId ? <LoadingState label="Loading project context" /> :
    <Panel title="Run and source">
      <form onSubmit={event => void submit(event)}>
        <div className="form-grid">
          <label><span>Project</span><input name="projectName" required readOnly={Boolean(project.data)} value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="Proteomics" /></label>
          <label><span>Experiment</span><input name="experimentName" required list={projectId ? 'project-experiments' : undefined} value={experimentName} onChange={event => setExperimentName(event.target.value)} placeholder="Cohort 1" />{projectId && <datalist id="project-experiments">{experiments.data.map(experiment => <option value={experiment.name} key={experiment.id} />)}</datalist>}</label>
          <label><span>Sample</span><input name="sampleName" required placeholder="Sample 001" /></label>
          <label><span>Run name</span><input name="runName" required placeholder="sample-001-rep-1" /></label>
          <label><span>Source method</span><select value={sourceMode} onChange={event => setSourceMode(event.target.value as 'file' | 'path')}><option value="file">Upload a file</option><option value="path">Import an allowlisted server path</option></select></label>
          {sourceMode === 'file'
            ? <label><span>Source file</span><input type="file" required onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
            : <label><span>Server path</span><input value={sourcePath} required onChange={event => setSourcePath(event.target.value)} placeholder="/imports/acquisition.raw" /></label>}
        </div>
        <div className="import-actions"><Link className="button button-secondary" to={backPath}>Cancel</Link><button type="submit" className="button button-primary" disabled={submitting || (sourceMode === 'file' && !file)}><Upload size={16} /> {submitting ? 'Importing' : 'Import run'}</button></div>
      </form>
    </Panel>}
  </>
}
