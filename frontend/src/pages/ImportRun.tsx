import { ArrowLeft, RotateCw, Upload, X } from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ImportDestination, type ImportProgress } from '../api/client'
import { useResource } from '../api/useResource'
import { formatBytes } from '../components/Data'
import { LoadingState, PageHeader, Panel } from '../components/Page'
import { projectRunsPath } from '../navigation'
import type { Project } from '../types'

type ImportItem = {
  id: string
  sourceName: string
  file?: File
  sourcePath?: string
  runName: string
  sampleName: string
  status: 'queued' | 'importing' | 'complete' | 'failed'
  progress?: ImportProgress
  error?: string
  result?: { id: string, projectId: string }
}

function sourceBase(name: string): string {
  const filename = name.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? name
  return filename.replace(/\.(mzml|mzxml|mgf|ms2|msp|raw|wiff|d)(\.gz)?$/i, '').slice(0, 255) || filename
}

function importKey(): string {
  // LAN installations can use HTTP, where randomUUID is unavailable.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function ImportRun() {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project') ?? ''
  const project = useResource<Project | null>(() => projectId ? api.project(projectId) : Promise.resolve(null), null, projectId)
  const experiments = useResource(() => projectId ? api.experiments(projectId) : Promise.resolve([]), [], projectId)
  const [sourceMode, setSourceMode] = useState<'file' | 'path'>('file')
  const [projectName, setProjectName] = useState('')
  const [experimentName, setExperimentName] = useState('')
  const [sourcePaths, setSourcePaths] = useState('')
  const [items, setItems] = useState<ImportItem[]>([])
  const [sharedSample, setSharedSample] = useState('')
  const [namePattern, setNamePattern] = useState('{name}')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [started, setStarted] = useState(false)
  const destination = useRef<ImportDestination | null>(null)
  const busy = useRef(false)
  const mounted = useRef(true)
  const backPath = projectId ? projectRunsPath(projectId) : '/projects'

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (project.data) setProjectName(project.data.name)
  }, [project.data])

  useEffect(() => {
    if (experiments.data[0]) setExperimentName(current => current || experiments.data[0].name)
  }, [experiments.data])

  useEffect(() => {
    if (!submitting) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [submitting])

  const update = (id: string, changes: Partial<ImportItem>) => {
    if (mounted.current) setItems(current => current.map(item => item.id === id ? { ...item, ...changes } : item))
  }

  const addSources = (sources: { file?: File, sourcePath?: string }[]) => {
    setError(null)
    setItems(current => {
      const next = [...current]
      for (const source of sources) {
        if (next.some(item => source.file
          ? item.file?.name === source.file.name && item.file.size === source.file.size && item.file.lastModified === source.file.lastModified
          : item.sourcePath === source.sourcePath)) continue
        const sourceName = source.file?.name ?? source.sourcePath ?? ''
        const name = sourceBase(sourceName)
        next.push({ ...source, id: importKey(), sourceName, runName: name, sampleName: name, status: 'queued' })
      }
      return next
    })
  }

  const applyNames = () => {
    if (!namePattern.trim() || /\{(?!name\}|index\})[^}]*\}/.test(namePattern)) {
      setError('Use a name pattern containing {name}, {index}, or plain text.')
      return
    }
    setError(null)
    setItems(current => current.map((item, index) => ({
      ...item, runName: namePattern.replaceAll('{name}', sourceBase(item.sourceName)).replaceAll('{index}', String(index + 1))
    })))
  }

  const runQueue = async (candidates: ImportItem[]) => {
    if (busy.current || !candidates.length) return
    if (!projectName.trim() || !experimentName.trim() || candidates.some(item => !item.runName.trim() || !item.sampleName.trim() || item.runName.length > 255 || item.sampleName.length > 255)) {
      setError('Enter a project, experiment, and a run and sample name of up to 255 characters for every item.')
      return
    }
    busy.current = true
    setSubmitting(true)
    setStarted(true)
    setError(null)
    try {
      destination.current ??= await api.prepareImport({ projectId: projectId || undefined, projectName, experimentName })
      for (const item of candidates) {
        if (!mounted.current) break
        update(item.id, { status: 'importing', error: undefined, progress: { phase: 'Preparing run' } })
        try {
          const result = await api.importRun({
            projectName, experimentName, destination: destination.current,
            sampleName: item.sampleName, runName: item.runName,
            file: item.file, sourcePath: item.sourcePath, idempotencyKey: item.id,
            onProgress: progress => update(item.id, { progress })
          })
          update(item.id, { status: 'complete', result, progress: undefined })
        } catch (reason) {
          update(item.id, { status: 'failed', progress: undefined, error: reason instanceof Error ? reason.message : 'Import failed' })
        }
      }
    } catch (reason) {
      if (mounted.current) {
        setError(reason instanceof Error ? reason.message : 'Could not prepare the import destination')
        setStarted(false)
      }
    } finally {
      busy.current = false
      if (mounted.current) setSubmitting(false)
    }
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void runQueue(items.filter(item => item.status === 'queued'))
  }
  const completed = items.filter(item => item.status === 'complete').length
  const failed = items.filter(item => item.status === 'failed').length
  const queued = items.filter(item => item.status === 'queued').length
  const resultProject = items.find(item => item.result)?.result?.projectId

  return <>
    {!submitting && <Link className="back-link" to={backPath}><ArrowLeft size={15} /> {project.data?.name ?? 'Projects'}</Link>}
    <PageHeader title="Import mass spectrometry data" description="Queue acquisitions, review their names, and import them into one project and experiment." />
    {(error || project.error || experiments.error) && <div className="message-banner" role="alert">{error ?? project.error ?? experiments.error}</div>}
    {(project.loading || experiments.loading) && projectId ? <LoadingState label="Loading project context" /> :
    <form onSubmit={submit}>
      <Panel title="Destination">
        <div className="form-grid">
          <label><span>Project</span><input name="projectName" required maxLength={255} readOnly={Boolean(project.data) || started} value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="Proteomics" /></label>
          <label><span>Experiment</span><input name="experimentName" required maxLength={255} readOnly={started} list={projectId ? 'project-experiments' : undefined} value={experimentName} onChange={event => setExperimentName(event.target.value)} placeholder="Cohort 1" />{projectId && <datalist id="project-experiments">{experiments.data.map(experiment => <option value={experiment.name} key={experiment.id} />)}</datalist>}</label>
        </div>
      </Panel>
      {!started && <Panel title="Sources" subtitle="Choose multiple files, or add server paths. Each acquisition becomes one run.">
        <div className="form-grid metadata-form-grid">
          <label><span>Source method</span><select value={sourceMode} onChange={event => setSourceMode(event.target.value as 'file' | 'path')}><option value="file">Upload files</option><option value="path">Import allowlisted server paths</option></select></label>
          {sourceMode === 'file'
            ? <label><span>Source files</span><input type="file" multiple onChange={event => {
              addSources(Array.from(event.target.files ?? []).map(file => ({ file })))
              event.target.value = ''
            }} /></label>
            : <label><span>Server paths, one per line</span><textarea value={sourcePaths} onChange={event => setSourcePaths(event.target.value)} placeholder={'/imports/acquisition.raw\n/imports/acquisition.d'} /><button type="button" className="button button-secondary" disabled={!sourcePaths.trim()} onClick={() => {
              addSources(sourcePaths.split(/\r?\n/).map(path => path.trim()).filter(Boolean).map(sourcePath => ({ sourcePath })))
              setSourcePaths('')
            }}>Add paths</button></label>}
        </div>
        <p className="batch-import-note">For a vendor directory such as Bruker .d, use its server path or the acquisition agent.</p>
      </Panel>}
      {items.length > 0 && <Panel title={`Import queue (${items.length})`} subtitle={started ? 'Keep this page open while imports run. Retry failed items here.' : 'Names start from each filename. Edit individual rows or apply names to the whole queue.'}>
        {!started && <div className="form-grid batch-import-naming">
          <label><span>Run name pattern</span><input value={namePattern} maxLength={255} onChange={event => setNamePattern(event.target.value)} /><small>Use {'{name}'} for the filename and {'{index}'} for its position.</small><button type="button" className="button button-secondary" onClick={applyNames}>Apply run names</button></label>
          <label><span>Sample for all runs</span><input value={sharedSample} maxLength={255} onChange={event => setSharedSample(event.target.value)} placeholder="Leave individual sample names, or enter a shared sample" /><button type="button" className="button button-secondary" disabled={!sharedSample.trim()} onClick={() => setItems(current => current.map(item => ({ ...item, sampleName: sharedSample.trim() })))}>Apply sample</button></label>
        </div>}
        <div className="table-scroll"><table className="batch-import-table">
          <thead><tr><th>Source</th><th>Run name</th><th>Sample</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{items.map((item, index) => <tr key={item.id}>
            <td><strong>{item.sourceName}</strong>{item.file && <small>{formatBytes(item.file.size)}</small>}</td>
            <td><input aria-label={`Run name ${index + 1}`} required maxLength={255} readOnly={started} value={item.runName} onChange={event => update(item.id, { runName: event.target.value })} /></td>
            <td><input aria-label={`Sample ${index + 1}`} required maxLength={255} readOnly={started} value={item.sampleName} onChange={event => update(item.id, { sampleName: event.target.value })} /></td>
            <td aria-live="polite">
              {item.status === 'queued' && 'Queued'}
              {item.status === 'complete' && <span className="batch-import-complete">Imported</span>}
              {item.status === 'failed' && <span className="batch-import-error">Failed: {item.error}</span>}
              {item.status === 'importing' && <><span>{item.progress?.phase ?? 'Importing'}{item.progress?.percent !== undefined ? ` ${item.progress.percent}%` : ''}</span><progress aria-label={`Import progress for ${item.sourceName}`} max={100} value={item.progress?.percent} /></>}
            </td>
            <td>{!started && <button type="button" className="icon-button" aria-label={`Remove ${item.sourceName}`} onClick={() => setItems(current => current.filter(row => row.id !== item.id))}><X size={16} /></button>}
              {item.status === 'failed' && <button type="button" className="button button-secondary" disabled={submitting} onClick={() => void runQueue([item])}><RotateCw size={14} /> Retry {item.runName}</button>}
              {item.result && <Link className="button button-secondary" to={`/projects/${item.result.projectId}/runs/${item.result.id}`} onClick={event => { if (submitting) event.preventDefault() }} aria-disabled={submitting}>Open {item.runName}</Link>}
            </td>
          </tr>)}</tbody>
        </table></div>
        <p className="batch-import-note" role="status">{completed} of {items.length} imported{failed ? `, ${failed} failed` : ''}{queued ? `, ${queued} queued` : ''}</p>
      </Panel>}
      <div className="import-actions">
        {!submitting && <Link className="button button-secondary" to={resultProject ? projectRunsPath(resultProject) : backPath}>{resultProject ? 'View project runs' : 'Cancel'}</Link>}
        {!submitting && started && queued === 0 && <button type="button" className="button button-secondary" onClick={() => {
          setItems([])
          setStarted(false)
          destination.current = null
          setError(null)
        }}>New batch</button>}
        {failed > 0 && <button type="button" className="button button-secondary" disabled={submitting} onClick={() => void runQueue(items.filter(item => item.status === 'failed'))}><RotateCw size={16} /> Retry failed ({failed})</button>}
        {(queued > 0 || !started) && <button type="submit" className="button button-primary" disabled={submitting || !queued || Boolean(project.error || experiments.error)}><Upload size={16} /> {submitting ? 'Importing' : `Import ${queued} ${queued === 1 ? 'run' : 'runs'}`}</button>}
      </div>
    </form>}
  </>
}
