import { Check, ChevronRight, Download, FileCheck2, FileOutput, Play, ShieldCheck, ScanLine } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, NavLink, useParams } from 'react-router-dom'
import { api, downloadArtifact } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useResource } from '../api/useResource'
import { SpectrumExplorer } from '../components/SpectrumExplorer'
import { ArtifactLocation } from '../components/ArtifactLocation'
import { formatBytes, formatRelativeDate, ProgressBar, RunStatusBadge } from '../components/Data'
import { ApiErrorBanner, LoadingState, PageHeader, Panel } from '../components/Page'
import { projectRunsPath, runPath, type RunDetailTab } from '../navigation'
import type { Artifact, ConversionFormat, Job, Run } from '../types'

const runTabs: Array<{ key: RunDetailTab, label: string }> = [
  { key: 'summary', label: 'Summary' },
  { key: 'spectra', label: 'Spectra' },
  { key: 'files', label: 'Files' },
  { key: 'processing', label: 'Processing' },
  { key: 'provenance', label: 'Provenance' }
]

export function RunDetail() {
  const auth = useAuth()
  const canProcess = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  const { projectId: routeProjectId, runId = '', tab = 'summary' } = useParams()
  const [generationStatus, setGenerationStatus] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)
  const resource = useResource<Run | null>(() => api.run(runId), null, runId, `run:${runId}`)
  const routeChanging = resource.data !== null && resource.data.id !== runId
  const run = routeChanging ? null : resource.data
  const currentRunId = useRef(runId)
  currentRunId.current = runId
  const refreshRun = useRef(resource.refresh)
  refreshRun.current = resource.refresh
  const [watchedJobs, setWatchedJobs] = useState<{ runId: string, ids: string[] }>({ runId, ids: [] })
  const validTab = runTabs.some(item => item.key === tab)
  const activeTab = validTab ? tab as RunDetailTab : 'summary'

  useEffect(() => {
    setGenerationStatus(null)
    setGenerating(false)
    setExtracting(false)
    setDownloading(null)
  }, [runId])

  useEffect(() => {
    const ids = watchedJobs.runId === runId ? watchedJobs.ids : []
    if (run?.status !== 'processing' && ids.length === 0) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const jobs = await Promise.all(ids.map(id => api.job(id)))
        if (!active) return
        const finished = jobs.filter(job => !['queued', 'running'].includes(job.status))
        if (finished.length) {
          setWatchedJobs(current => current.runId === runId
            ? { runId, ids: current.ids.filter(id => !finished.some(job => job.id === id)) }
            : current)
          setGenerationStatus(finished.map(job => `Job ${job.id} is ${job.status}${job.status === 'failed' && job.detail ? `: ${job.detail}` : ''}`).join('. '))
        }
      } catch (error) {
        if (active) setGenerationStatus(error instanceof Error ? error.message : 'Could not refresh processing status')
      } finally {
        if (active) {
          refreshRun.current()
          timer = setTimeout(() => void poll(), 1500)
        }
      }
    }
    timer = setTimeout(() => void poll(), 1500)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [runId, run?.status, watchedJobs])

  if ((resource.loading || routeChanging) && !run) return <>
    <nav className="breadcrumb" aria-label="Breadcrumb"><Link to="/projects">Projects</Link><ChevronRight size={13} /><span>Loading run</span></nav>
    <PageHeader title="Loading run" description="Fetching run metadata, files, and processing state." />
    <LoadingState label="Loading run details" />
  </>

  if (!run) return <>
    <nav className="breadcrumb" aria-label="Breadcrumb"><Link to="/projects">Projects</Link><ChevronRight size={13} /><span>Run unavailable</span></nav>
    <PageHeader title="Run unavailable" description="MassSpec could not load live data for this run." />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
  </>

  const sourceArtifact = [...run.artifacts].reverse().find(artifact => artifact.role === 'source' && artifact.status === 'verified')
    ?? run.artifacts.find(artifact => artifact.role === 'source')
  const projectId = run.projectId ?? ''
  const canonicalPath = runPath(run, activeTab)
  const routeMatchesProject = routeProjectId === undefined ? !projectId : routeProjectId === projectId
  if (!validTab || !routeMatchesProject) return <Navigate to={canonicalPath} replace />

  const generate = async (format: ConversionFormat) => {
    setGenerating(true)
    setGenerationStatus(null)
    try {
      const job = await api.generateArtifact(run.id, format, sourceArtifact?.id)
      if (currentRunId.current !== run.id) return
      setGenerationStatus(`${format} job ${job.id || 'queued'} is ${job.status || 'queued'}`)
      if (['queued', 'running'].includes(job.status)) setWatchedJobs(current => ({ runId, ids: [...new Set([...(current.runId === runId ? current.ids : []), job.id])] }))
      resource.refresh()
    } catch (error) {
      if (currentRunId.current === run.id) setGenerationStatus(error instanceof Error ? error.message : `Could not queue ${format}`)
    } finally {
      if (currentRunId.current === run.id) setGenerating(false)
    }
  }

  const extract = async () => {
    if (!sourceArtifact) return
    setExtracting(true)
    setGenerationStatus(null)
    try {
      const job = await api.extractArtifact(sourceArtifact.id, Boolean(run.extraction))
      if (currentRunId.current !== run.id) return
      setGenerationStatus(`Metadata extraction job ${job.id} is ${job.status}`)
      if (['queued', 'running'].includes(job.status)) setWatchedJobs(current => ({ runId, ids: [...new Set([...(current.runId === runId ? current.ids : []), job.id])] }))
      resource.refresh()
    } catch (error) {
      if (currentRunId.current === run.id) setGenerationStatus(error instanceof Error ? error.message : 'Could not queue metadata extraction')
    } finally {
      if (currentRunId.current === run.id) setExtracting(false)
    }
  }

  const download = async (artifactId: string, filename: string) => {
    setDownloading(artifactId)
    setGenerationStatus(null)
    try {
      const blob = await downloadArtifact(artifactId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (error) {
      setGenerationStatus(error instanceof Error ? error.message : 'Could not download artifact')
    } finally {
      setDownloading(null)
    }
  }

  return <>
    <nav className="breadcrumb" aria-label="Breadcrumb">
      <Link to="/projects">Projects</Link>
      <ChevronRight size={13} />
      {projectId ? <Link to={projectRunsPath(projectId)}>{run.projectName}</Link> : <span>{run.projectName}</span>}
      <ChevronRight size={13} />
      <span>{run.name}</span>
    </nav>
    <div className="run-hero">
      <div className="run-title-block">
        <div><div className="title-status"><h1>{run.name}</h1><RunStatusBadge status={run.status} /></div><p>{run.experimentName} · {run.sampleName} · {run.sourceFormat}</p></div>
      </div>
      <div className="page-actions">{sourceArtifact && !sourceArtifact.isDirectory && sourceArtifact.status === 'verified' && <button className="button button-secondary" disabled={downloading === sourceArtifact.id} onClick={() => void download(sourceArtifact.id, sourceArtifact.name)}><Download size={16} /> {downloading === sourceArtifact.id ? 'Downloading' : 'Download source'}</button>}</div>
    </div>
    <nav className="section-tabs run-tabs" aria-label="Run sections">
      {runTabs.map(item => <NavLink key={item.key} to={runPath(run, item.key)} end={item.key === 'summary'} className={item.key === activeTab ? 'active' : ''}>{item.label}</NavLink>)}
    </nav>
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {generationStatus && <div className="message-banner" role="status">{generationStatus}</div>}

    {activeTab === 'summary' && <RunSummary run={run} />}
    {activeTab === 'spectra' && <Panel title="Spectrum viewer" subtitle="Search, filter, and inspect spectra without leaving this run">
      <SpectrumExplorer canBuild={canProcess} artifacts={run.artifacts} preferredMsLevel={preferredSpectrumMsLevel(run)} spectrumCounts={run.extraction?.spectraByMsLevel} chromatogram={run.extraction?.tic ?? []} />
    </Panel>}
    {activeTab === 'files' && <RunFiles run={run} downloading={downloading} onDownload={download} />}
    {activeTab === 'processing' && <RunProcessing run={run} sourceArtifact={sourceArtifact} canProcess={canProcess} generating={generating} extracting={extracting} onGenerate={generate} onExtract={extract} />}
    {activeTab === 'provenance' && <RunProvenance run={run} sourceArtifact={sourceArtifact} />}
  </>
}

function RunSummary({ run }: { run: Run }) {
  return <>
    <div className="detail-stat-grid">
      <div><span>Instrument</span><strong>{run.instrument}</strong></div>
      <div><span>Acquired</span><strong>{run.acquiredAt ? new Date(run.acquiredAt).toLocaleDateString() : 'Unknown'}</strong></div>
      <div><span>Duration</span><strong>{formatDuration(run.durationMinutes)}</strong></div>
      <div><span>Total spectra</span><strong>{run.spectraCount?.toLocaleString() ?? 'Unknown'}</strong></div>
      <div><span>MS2 spectra</span><strong>{run.ms2Count?.toLocaleString() ?? 'Unknown'}</strong></div>
    </div>
    <div className="detail-layout">
      <div className="detail-main">
        <Panel title="Scientific metadata" subtitle={run.extraction?.artifactName ? `Observed in ${run.extraction.artifactName}` : 'Versioned observations from an identified artifact'}>
          {run.extraction?.selectionReason === 'linked_open_format_fallback' && <p className="panel-note">Source metadata is unavailable. These values describe a linked converted file and may differ from the original acquisition.</p>}
          {run.extraction ? <div className="science-grid">
            <ScienceValue label="MS levels" value={Object.entries(run.extraction.spectraByMsLevel).map(([level, count]) => `MS${level}: ${count.toLocaleString()}`).join(' · ') || 'Unknown'} />
            <ScienceValue label="Polarity" value={run.extraction.polarities.join(', ') || 'Unknown'} />
            <ScienceValue label="Representation" value={run.extraction.representation ?? 'Unknown'} />
            <ScienceValue label="m/z range" value={run.extraction.mzRange ? `${run.extraction.mzRange[0].toLocaleString()} to ${run.extraction.mzRange[1].toLocaleString()}` : 'Unknown'} />
            <ScienceValue label="Precursors" value={run.extraction.precursorCount?.toLocaleString() ?? 'Unknown'} />
            <ScienceValue label="Mean peaks per spectrum" value={run.extraction.peakCountMean?.toLocaleString() ?? 'Unknown'} />
            <ScienceValue label="Collision energy" value={run.extraction.collisionEnergyRange ? `${run.extraction.collisionEnergyRange[0]} to ${run.extraction.collisionEnergyRange[1]}` : 'Unknown'} />
            <ScienceValue label="Ion mobility" value={run.extraction.ionMobility === undefined ? 'Unknown' : run.extraction.ionMobility ? 'Present' : 'Not present'} />
          </div> : <div className="settings-placeholder"><ScanLine size={19} /> Metadata extraction has not completed for this source.</div>}
          {run.extraction?.warnings.length ? <div className="extraction-warnings"><strong>Extractor warnings</strong>{run.extraction.warnings.map(warning => <span key={warning}>{warning}</span>)}</div> : null}
        </Panel>
      </div>
      <aside className="detail-side">
        <Panel title="Run details">
          <dl className="metadata-list">
            <div><dt>Project</dt><dd>{run.projectName}</dd></div>
            <div><dt>Experiment</dt><dd>{run.experimentName}</dd></div>
            <div><dt>Sample</dt><dd>{run.sampleName}</dd></div>
            <div><dt>Source type</dt><dd>{run.sourceFormat === 'RAW' ? 'Vendor acquisition' : `Imported ${run.sourceFormat}`}</dd></div>
            <div><dt>Total stored</dt><dd>{formatBytes(run.sizeBytes)}</dd></div>
            <div><dt>Imported</dt><dd>{formatRelativeDate(run.importedAt)}</dd></div>
          </dl>
        </Panel>
      </aside>
    </div>
  </>
}

function RunFiles({ run, downloading, onDownload }: { run: Run, downloading: string | null, onDownload: (id: string, name: string) => Promise<void> }) {
  return <Panel title="Files" subtitle="Immutable source data and generated derivatives">
    <div className="artifact-list">
      {run.artifacts.map(item => <div key={item.id}><div className="artifact-row">
        <div className="artifact-icon"><FileCheck2 size={19} /></div>
        <div className="artifact-primary"><strong>{item.name}</strong><span title={item.libraryPath}>{item.libraryPath ?? `${item.role} · ${formatBytes(item.sizeBytes)}`}</span></div>
        <span className="format-chip">{item.format}</span>
        <span className={item.status === 'verified' ? 'verified' : 'purged'}><ShieldCheck size={14} /> {item.status === 'verified' ? 'stored' : item.status}</span>
        <button className="icon-button" disabled={item.status !== 'verified' || item.isDirectory || downloading === item.id} onClick={() => void onDownload(item.id, item.name)} aria-label={`Download ${item.name}`}><Download size={16} /></button>
      </div><ArtifactLocation artifactId={item.id} /></div>)}
    </div>
  </Panel>
}

function RunProcessing({ run, sourceArtifact, canProcess, generating, extracting, onGenerate, onExtract }: {
  run: Run
  sourceArtifact?: Artifact
  canProcess: boolean
  generating: boolean
  extracting: boolean
  onGenerate: (format: ConversionFormat) => Promise<void>
  onExtract: () => Promise<void>
}) {
  const sourceReady = sourceArtifact?.status === 'verified'
  const jobs = run.processingJobs ?? []
  const activeJobs = jobs.filter(job => ['queued', 'running'].includes(job.status))
  const attentionJobs = jobs.filter(job => ['queued', 'running', 'failed'].includes(job.status))
  const finishedJobs = jobs.filter(job => ['complete', 'cancelled'].includes(job.status))
  const extractionActive = activeJobs.some(job => job.kind === 'extract_metadata' && job.inputArtifactId === sourceArtifact?.id)
  const outputs = run.artifacts.filter(artifact => artifact.role === 'derived' && artifact.status === 'verified')
  const actions: Array<[ConversionFormat, string]> = [
    ['mzML', 'Open format for analysis and data exchange. Original acquisition files are retained.'],
    ['MGF', 'MS2 peak lists using the standard centroiding profile. Check compatibility with your search tool.'],
    ['mzXML', 'XML interchange for tools that require mzXML.'],
    ['MS2', 'Text peak lists for tools that require MS2.']
  ]
  const availableActions = actions.filter(([format]) => format !== sourceArtifact?.format)
  const action = ([format, description]: [ConversionFormat, string]) => {
    const active = activeJobs.find(job => job.kind === 'convert' && job.outputFormat === format && job.inputArtifactId === sourceArtifact?.id)
    return <button className="artifact-action" key={format} disabled={!canProcess || generating || !sourceReady || Boolean(active)} onClick={() => void onGenerate(format)}>
      <FileOutput size={17} /><span><strong>{active ? `${format} ${active.status}` : `Generate ${format}`}</strong><small>{description}</small></span><Play size={15} />
    </button>
  }
  const jobRow = (job: Job) => <div className="processing-job" key={job.id}>
    <div><strong>{job.kind === 'convert' ? `${job.outputFormat ?? 'File'} conversion` : job.kind === 'extract_metadata' ? 'Metadata extraction' : job.kind.replaceAll('_', ' ')}</strong><span>{job.status}</span></div>
    <small>{run.artifacts.find(artifact => artifact.id === job.inputArtifactId)?.name}</small>
    {job.status === 'running' && <ProgressBar value={job.progress} />}
    {job.detail && <p>{job.detail}</p>}
    {job.status === 'failed' && <Link to="/processing">Review and retry in Processing</Link>}
  </div>
  return <div className="detail-layout">
    <div className="detail-main">
      <Panel title="Generate a file" subtitle={sourceReady ? `Convert from ${sourceArtifact.name}` : 'A stored source file is required for processing'}>
        {!canProcess && <p className="panel-note">An operator or administrator can start processing.</p>}
        {availableActions.filter(([format]) => ['mzML', 'MGF'].includes(format)).map(action)}
        <details className="processing-formats"><summary>Other formats</summary>{availableActions.filter(([format]) => !['mzML', 'MGF'].includes(format)).map(action)}</details>
        <p className="panel-note">Matching outputs are reused. Manage custom profiles and regeneration in <Link to="/processing">Processing</Link>.</p>
      </Panel>
      {outputs.length > 0 && <Panel title="Available outputs" subtitle="Stored derivatives. Open Files to inspect provenance and location.">
        {outputs.map(output => <div className="processing-result" key={output.id}><FileCheck2 size={17} /><span>{output.name}</span><Link to={runPath(run, 'files')}>View file</Link></div>)}
      </Panel>}
      {jobs.length > 0 && <Panel title="Processing activity" subtitle="Latest attempt for each input and profile">
        {attentionJobs.map(jobRow)}
        {finishedJobs.length > 0 && <details className="processing-history"><summary>{finishedJobs.length} finished {finishedJobs.length === 1 ? 'job' : 'jobs'}</summary>{finishedJobs.map(jobRow)}</details>}
      </Panel>}
    </div>
    <aside className="detail-side">
      <Panel title="Metadata extraction">
        <dl className="metadata-list">
          <div><dt>Status</dt><dd>{extractionActive ? 'Processing' : run.extraction?.status ?? 'Not extracted'}</dd></div>
          <div><dt>Provider</dt><dd>{run.extraction ? `${run.extraction.extractor} ${run.extraction.extractorVersion}` : 'None'}</dd></div>
          <div><dt>Completed</dt><dd>{run.extraction?.finishedAt ? formatRelativeDate(run.extraction.finishedAt) : 'Not completed'}</dd></div>
        </dl>
        {sourceArtifact && <div className="panel-action-row"><button className="button button-secondary button-small" disabled={!canProcess || extracting || !sourceReady || extractionActive} onClick={() => void onExtract()}><ScanLine size={14} />{extracting ? 'Queuing' : extractionActive ? 'Extraction in progress' : run.extraction ? 'Re-extract metadata' : 'Extract metadata'}</button></div>}
      </Panel>
    </aside>
  </div>
}

function RunProvenance({ run, sourceArtifact }: { run: Run, sourceArtifact?: Artifact }) {
  return <div className="detail-layout">
    <div className="detail-main">
      <Panel title="Source integrity" subtitle="Checksums and immutable source identity">
        {sourceArtifact ? <><div className="integrity"><div className="integrity-badge"><Check size={20} /></div><div><strong>Source {sourceArtifact.status === 'verified' ? 'stored' : sourceArtifact.status}</strong><span>SHA-256 recorded during import, not reverified here</span></div></div><div className="checksum mono">{sourceArtifact.checksum}</div></> : <div className="settings-placeholder">This run has no source artifact.</div>}
      </Panel>
    </div>
    <aside className="detail-side">
      <Panel title="Extraction provenance">
        <dl className="metadata-list">
          <div><dt>Run ID</dt><dd className="mono">{run.id}</dd></div>
          <div><dt>Status</dt><dd>{run.extraction?.status ?? 'Not extracted'}</dd></div>
          <div><dt>Provider</dt><dd>{run.extraction ? `${run.extraction.extractor} ${run.extraction.extractorVersion}` : 'None'}</dd></div>
          <div><dt>Schema</dt><dd>{run.extraction?.schemaVersion ?? 'None'}</dd></div>
          <div><dt>Completed</dt><dd>{run.extraction?.finishedAt ? formatRelativeDate(run.extraction.finishedAt) : 'Not completed'}</dd></div>
        </dl>
      </Panel>
    </aside>
  </div>
}

function formatDuration(minutes?: number) {
  if (minutes === undefined) return 'Unknown'
  if (minutes < 1) return `${(minutes * 60).toFixed(1).replace(/\.0$/, '')} sec`
  return `${minutes.toFixed(1).replace(/\.0$/, '')} min`
}

function preferredSpectrumMsLevel(run: Run): 1 | 2 {
  const levels = run.extraction?.spectraByMsLevel
  if (levels && !levels['1'] && levels['2']) return 2
  return 1
}

function ScienceValue({ label, value }: { label: string, value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}
