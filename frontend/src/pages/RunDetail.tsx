import { ArrowLeft, Check, Download, FileCheck2, FileOutput, Play, ShieldCheck, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, downloadArtifact } from '../api/client'
import { useResource } from '../api/useResource'
import { formatBytes, formatRelativeDate, RunStatusBadge } from '../components/Data'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'
import { SpectrumExplorer } from '../components/SpectrumExplorer'
import type { ConversionFormat, Run } from '../types'

export function RunDetail() {
  const { runId = '' } = useParams()
  const [generationStatus, setGenerationStatus] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)
  const resource = useResource<Run | null>(() => api.run(runId), null)
  const run = resource.data

  if (!run) return <>
    <Link className="back-link" to="/runs"><ArrowLeft size={15} /> All runs</Link>
    <PageHeader title="Run unavailable" description="Spectarr could not load live data for this run." />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
  </>

  const sourceArtifact = run.artifacts.find(artifact => artifact.role === 'source')

  const generate = async (format: ConversionFormat) => {
    setGenerating(true)
    setGenerationStatus(null)
    try {
      const job = await api.generateArtifact(run.id, format)
      setGenerationStatus(`${format} job ${job.id || 'queued'} is ${job.status || 'queued'}`)
    } catch (error) {
      setGenerationStatus(error instanceof Error ? error.message : `Could not queue ${format}`)
    } finally {
      setGenerating(false)
    }
  }

  const extract = async () => {
    if (!sourceArtifact) return
    setExtracting(true)
    setGenerationStatus(null)
    try {
      const job = await api.extractArtifact(sourceArtifact.id, Boolean(run.extraction))
      setGenerationStatus(`Metadata extraction job ${job.id} is ${job.status}`)
    } catch (error) {
      setGenerationStatus(error instanceof Error ? error.message : 'Could not queue metadata extraction')
    } finally {
      setExtracting(false)
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
    <Link className="back-link" to="/runs"><ArrowLeft size={15} /> All runs</Link>
    <div className="run-hero">
      <div className="run-title-block">
        <div className="file-glyph file-glyph-large">{run.sourceFormat}</div>
        <div><div className="title-status"><h1>{run.name}</h1><RunStatusBadge status={run.status} /></div><p>{run.projectName} / {run.sampleName}</p></div>
      </div>
      <div className="page-actions">{sourceArtifact && <button className="button button-secondary" disabled={downloading === sourceArtifact.id} onClick={() => void download(sourceArtifact.id, sourceArtifact.name)}><Download size={16} /> {downloading === sourceArtifact.id ? 'Downloading' : 'Download source'}</button>}{sourceArtifact && <button className="button button-primary" disabled={generating} onClick={() => void generate('mzML')}><FileOutput size={16} /> {generating ? 'Queuing' : 'Generate mzML'}</button>}</div>
    </div>
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {generationStatus && <div className="message-banner" role="status">{generationStatus}</div>}

    <div className="detail-stat-grid">
      <div><span>Instrument</span><strong>{run.instrument}</strong></div>
      <div><span>Acquired</span><strong>{new Date(run.acquiredAt).toLocaleDateString()}</strong></div>
      <div><span>Duration</span><strong>{formatDuration(run.durationMinutes)}</strong></div>
      <div><span>Total spectra</span><strong>{run.spectraCount?.toLocaleString() ?? 'Unknown'}</strong></div>
      <div><span>MS2 spectra</span><strong>{run.ms2Count?.toLocaleString() ?? 'Unknown'}</strong></div>
    </div>

    <div className="detail-layout">
      <div className="detail-main">
        <Panel title="Spectrum viewer" subtitle="Select from the chromatogram, search directly, or browse nearby spectra">
          <SpectrumExplorer artifacts={run.artifacts} preferredMsLevel={preferredSpectrumMsLevel(run)} spectrumCounts={run.extraction?.spectraByMsLevel} chromatogram={run.extraction?.tic ?? []} />
        </Panel>
        <Panel title="Scientific metadata" subtitle="Versioned observations extracted from the source artifact">
          {run.extraction ? <div className="science-grid">
            <ScienceValue label="MS levels" value={Object.entries(run.extraction.spectraByMsLevel).map(([level, count]) => `MS${level}: ${count.toLocaleString()}`).join(' · ') || 'Unknown'} />
            <ScienceValue label="Polarity" value={run.extraction.polarities.join(', ') || 'Unknown'} />
            <ScienceValue label="Representation" value={run.extraction.representation ?? 'Unknown'} />
            <ScienceValue label="m/z range" value={run.extraction.mzRange ? `${run.extraction.mzRange[0].toLocaleString()} to ${run.extraction.mzRange[1].toLocaleString()}` : 'Unknown'} />
            <ScienceValue label="Precursors" value={run.extraction.precursorCount?.toLocaleString() ?? 'Unknown'} />
            <ScienceValue label="Mean peaks per spectrum" value={run.extraction.peakCountMean?.toLocaleString() ?? 'Unknown'} />
            <ScienceValue label="Collision energy" value={run.extraction.collisionEnergyRange ? `${run.extraction.collisionEnergyRange[0]} to ${run.extraction.collisionEnergyRange[1]}` : 'Unknown'} />
            <ScienceValue label="Ion mobility" value={run.extraction.ionMobility === undefined ? 'Unknown' : run.extraction.ionMobility ? 'Present' : 'Not present'} />
          </div> : <div className="settings-placeholder"><Sparkles size={19} /> Metadata extraction has not completed for this source.</div>}
          {run.extraction?.warnings.length ? <div className="extraction-warnings"><strong>Extractor warnings</strong>{run.extraction.warnings.map(warning => <span key={warning}>{warning}</span>)}</div> : null}
        </Panel>
        <Panel title="Artifacts" subtitle="Source data and generated derivatives">
          <div className="artifact-list">
            {run.artifacts.map(item => <div className="artifact-row" key={item.id}>
              <div className="artifact-icon"><FileCheck2 size={19} /></div>
              <div className="artifact-primary"><strong>{item.name}</strong><span title={item.libraryPath}>{item.libraryPath ?? `${item.role} · ${formatBytes(item.sizeBytes)}`}</span></div>
              <span className="format-chip">{item.format}</span>
              <span className={item.status === 'purged' ? 'purged' : 'verified'}><ShieldCheck size={14} /> {item.status}</span>
              <button className="icon-button" disabled={item.status === 'purged' || downloading === item.id} onClick={() => void download(item.id, item.name)} aria-label={`Download ${item.name}`}><Download size={16} /></button>
            </div>)}
          </div>
          {sourceArtifact && ([
            ['mzXML', 'Generate mzXML', 'Queue an open XML interchange derivative'],
            ['MGF', 'Generate search-ready MGF', 'Queue the default MGF processing profile'],
            ['MS2', 'Generate search-ready MS2', 'Queue the default MS2 processing profile']
          ] as const).map(([format, title, description]) => <button className="artifact-action" key={format} disabled={generating} onClick={() => void generate(format)}><FileOutput size={17} /><span><strong>{title}</strong><small>{description}</small></span><Play size={15} /></button>)}
        </Panel>
      </div>
      <aside className="detail-side">
        <Panel title="Run details">
          <dl className="metadata-list">
            <div><dt>Source type</dt><dd>{run.sourceFormat === 'RAW' ? 'Vendor acquisition' : `Imported ${run.sourceFormat}`}</dd></div>
            <div><dt>Total stored</dt><dd>{formatBytes(run.sizeBytes)}</dd></div>
            <div><dt>Imported</dt><dd>{formatRelativeDate(run.importedAt)}</dd></div>
            <div><dt>Run ID</dt><dd className="mono">{run.id}</dd></div>
          </dl>
        </Panel>
        <Panel title="Integrity">
          {sourceArtifact ? <><div className="integrity"><div className="integrity-badge"><Check size={20} /></div><div><strong>Source {sourceArtifact.status}</strong><span>SHA-256 recorded during import</span></div></div><div className="checksum mono">{sourceArtifact.checksum}</div></> : <div className="settings-placeholder">This run has no source artifact.</div>}
        </Panel>
        <Panel title="Extraction provenance">
          <dl className="metadata-list">
            <div><dt>Status</dt><dd>{run.extraction?.status ?? 'Not extracted'}</dd></div>
            <div><dt>Provider</dt><dd>{run.extraction ? `${run.extraction.extractor} ${run.extraction.extractorVersion}` : 'None'}</dd></div>
            <div><dt>Schema</dt><dd>{run.extraction?.schemaVersion ?? 'None'}</dd></div>
            <div><dt>Completed</dt><dd>{run.extraction?.finishedAt ? formatRelativeDate(run.extraction.finishedAt) : 'Not completed'}</dd></div>
          </dl>
          {run.artifacts.some(artifact => artifact.role === 'source') && <div className="panel-action-row"><button className="button button-secondary button-small" disabled={extracting} onClick={() => void extract()}><Sparkles size={14} />{extracting ? 'Queuing' : run.extraction ? 'Re-extract metadata' : 'Extract metadata'}</button></div>}
        </Panel>
      </aside>
    </div>
  </>
}

function formatDuration(minutes?: number) {
  if (!minutes) return 'Unknown'
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
