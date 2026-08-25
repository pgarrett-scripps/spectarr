import { ArrowLeft, Upload } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { PageHeader, Panel } from '../components/Page'

export function ImportRun() {
  const navigate = useNavigate()
  const [sourceMode, setSourceMode] = useState<'file' | 'path'>('file')
  const [file, setFile] = useState<File | null>(null)
  const [sourcePath, setSourcePath] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

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
      navigate(`/runs/${run.id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Import failed')
    } finally {
      setSubmitting(false)
    }
  }

  return <>
    <Link className="back-link" to="/runs"><ArrowLeft size={15} /> All runs</Link>
    <PageHeader title="Import mass spectrometry data" description="Create a run from vendor data, mzML, mzXML, MGF, or MS2." />
    {error && <div className="message-banner" role="alert">{error}</div>}
    <Panel title="Run and source">
      <form onSubmit={event => void submit(event)}>
        <div className="form-grid">
          <label><span>Project</span><input name="projectName" required placeholder="Proteomics" /></label>
          <label><span>Experiment</span><input name="experimentName" required placeholder="Cohort 1" /></label>
          <label><span>Sample</span><input name="sampleName" required placeholder="Sample 001" /></label>
          <label><span>Run name</span><input name="runName" required placeholder="sample-001-rep-1" /></label>
          <label><span>Source method</span><select value={sourceMode} onChange={event => setSourceMode(event.target.value as 'file' | 'path')}><option value="file">Upload a file</option><option value="path">Import an allowlisted server path</option></select></label>
          {sourceMode === 'file'
            ? <label><span>Source file</span><input type="file" required onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
            : <label><span>Server path</span><input value={sourcePath} required onChange={event => setSourcePath(event.target.value)} placeholder="/imports/acquisition.raw" /></label>}
        </div>
        <div className="import-actions"><Link className="button button-secondary" to="/runs">Cancel</Link><button type="submit" className="button button-primary" disabled={submitting || (sourceMode === 'file' && !file)}><Upload size={16} /> {submitting ? 'Importing' : 'Import run'}</button></div>
      </form>
    </Panel>
  </>
}
