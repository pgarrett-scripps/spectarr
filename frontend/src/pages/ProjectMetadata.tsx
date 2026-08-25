import { AlertTriangle, CheckCircle2, Download, FileArchive, FileCheck2, FileUp, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { formatBytes } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader, Panel } from '../components/Page'
import type { Project, SdrfDocument, SdrfTemplate, SdrfValidationReport, SubmissionPreview } from '../types'

const lines = (value: unknown): string => Array.isArray(value) ? value.join('\n') : ''
const list = (value: string): string[] => value.split(/\r?\n|,/).map(item => item.trim()).filter(Boolean)

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function ProjectMetadata() {
  const { projectId = '' } = useParams()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [project, setProject] = useState<Project>()
  const [document, setDocument] = useState<SdrfDocument>()
  const [templates, setTemplates] = useState<SdrfTemplate[]>([])
  const [preview, setPreview] = useState<SubmissionPreview>()
  const [validation, setValidation] = useState<SdrfValidationReport>()
  const [ontology, setOntology] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [metadata, setMetadata] = useState({
    keywords: '', contacts: '', publications: '', funding: '', license: '', repository: '', embargoDate: ''
  })

  const refreshPreview = useCallback(async () => setPreview(await api.submissionPreview(projectId)), [projectId])
  const load = useCallback(async () => {
    setError('')
    try {
      const [projectValue, templateValues, previewValue] = await Promise.all([
        api.project(projectId), api.sdrfTemplates(), api.submissionPreview(projectId)
      ])
      setProject(projectValue)
      setTemplates(templateValues)
      setPreview(previewValue)
      setMetadata({
        keywords: lines(projectValue.metadata.keywords),
        contacts: lines(projectValue.metadata.contacts),
        publications: lines(projectValue.metadata.publications),
        funding: lines(projectValue.metadata.funding),
        license: typeof projectValue.metadata.license === 'string' ? projectValue.metadata.license : '',
        repository: typeof projectValue.metadata.repository === 'string' ? projectValue.metadata.repository : '',
        embargoDate: typeof projectValue.metadata.embargo_date === 'string' ? projectValue.metadata.embargo_date : ''
      })
      try {
        const sdrf = await api.projectSdrf(projectId)
        setDocument(sdrf)
        setValidation(sdrf.validationReport)
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause
        setDocument(undefined)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load project metadata')
    }
  }, [projectId])

  useEffect(() => { void load() }, [load])

  const perform = async (label: string, action: () => Promise<void>) => {
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await action()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `${label} failed`)
    } finally {
      setBusy('')
    }
  }

  const saveProject = () => perform('Saving project metadata', async () => {
    if (!project) return
    const updated = await api.updateProject(projectId, {
      name: project.name,
      description: project.description ?? '',
      metadata: {
        ...project.metadata,
        keywords: list(metadata.keywords),
        contacts: list(metadata.contacts),
        publications: list(metadata.publications),
        funding: list(metadata.funding),
        license: metadata.license || null,
        repository: metadata.repository || null,
        embargo_date: metadata.embargoDate || null
      }
    })
    setProject(updated)
    setNotice('Project metadata saved')
  })

  const generate = () => perform('Generating SDRF', async () => {
    const value = await api.generateProjectSdrf(projectId)
    setDocument(value)
    setValidation(undefined)
    await refreshPreview()
    setNotice(`Generated ${value.rows.length} SDRF rows from the project`)
  })

  const saveSdrf = () => perform('Saving SDRF', async () => {
    if (!document) return
    const value = await api.saveProjectSdrf(projectId, document)
    setDocument(value)
    setValidation(undefined)
    await refreshPreview()
    setNotice(`Saved SDRF revision ${value.revision}`)
  })

  const validate = () => perform('Validating SDRF', async () => {
    const report = await api.validateProjectSdrf(projectId, ontology)
    setValidation(report)
    setDocument(current => current ? { ...current, status: report.valid ? 'valid' : 'invalid', validationReport: report } : current)
    await refreshPreview()
    setNotice(report.valid ? 'SDRF validation passed' : `Validation found ${report.errorCount} errors`)
  })

  const importFile = (file?: File) => {
    if (!file) return
    void perform('Importing SDRF', async () => {
      const value = await api.importProjectSdrf(projectId, file)
      setDocument(value)
      setValidation(undefined)
      await refreshPreview()
      setNotice(`Imported ${value.rows.length} SDRF rows and synchronized mapped samples`)
    })
  }

  const editCell = (rowIndex: number, columnIndex: number, value: string) => setDocument(current => {
    if (!current) return current
    return {
      ...current,
      status: 'draft',
      rows: current.rows.map((row, index) => index === rowIndex
        ? { ...row, values: row.values.map((cell, position) => position === columnIndex ? value : cell) }
        : row)
    }
  })

  const editColumn = (columnIndex: number, value: string) => setDocument(current => current ? {
    ...current,
    status: 'draft',
    columns: current.columns.map((column, index) => index === columnIndex ? value : column)
  } : current)

  const addColumn = () => {
    const name = window.prompt('SDRF column name, for example factor value[condition]')?.trim()
    if (!name) return
    setDocument(current => current ? {
      ...current,
      status: 'draft',
      columns: [...current.columns, name],
      rows: current.rows.map(row => ({ ...row, values: [...row.values, 'not available'] }))
    } : current)
  }

  const removeColumn = (columnIndex: number) => setDocument(current => current ? {
    ...current,
    status: 'draft',
    columns: current.columns.filter((_, index) => index !== columnIndex),
    rows: current.rows.map(row => ({ ...row, values: row.values.filter((_, index) => index !== columnIndex) }))
  } : current)

  const addRow = () => setDocument(current => current ? {
    ...current,
    status: 'draft',
    rows: [...current.rows, { position: current.rows.length, values: current.columns.map(() => 'not available') }]
  } : current)

  const removeRow = (rowIndex: number) => setDocument(current => current ? {
    ...current,
    status: 'draft',
    rows: current.rows.filter((_, index) => index !== rowIndex).map((row, position) => ({ ...row, position }))
  } : current)

  const toggleTemplate = (template: SdrfTemplate) => setDocument(current => {
    if (!current) return current
    const name = `${template.name} ${template.version}`
    const selected = current.templates.includes(name)
    return { ...current, status: 'draft', templates: selected ? current.templates.filter(value => value !== name) : [...current.templates, name] }
  })

  const exportSdrf = () => perform('Exporting SDRF', async () => {
    saveBlob(await api.downloadProjectSdrf(projectId), document?.sourceFilename ?? `${project?.name ?? 'project'}.sdrf.tsv`)
  })
  const exportSubmission = () => perform('Building repository package', async () => {
    saveBlob(await api.downloadSubmission(projectId), `${project?.name ?? 'project'}-repository-submission.zip`)
  })

  return <>
    <PageHeader eyebrow="Project metadata" title={project?.name ?? 'SDRF'} description="Edit repository-ready project metadata and the exact ordered SDRF table." actions={<>
      <Link className="button button-secondary" to={`/runs?project=${projectId}`}>Back to project</Link>
      <button className="button button-primary" disabled={!project || Boolean(busy)} onClick={() => void saveProject()}><Save size={16} /> Save project</button>
    </>} />
    {error && <ApiErrorBanner message={error} onRetry={() => void load()} />}
    {notice && <div className="message-banner metadata-success" role="status"><div><CheckCircle2 size={17} /><span>{notice}</span></div></div>}

    <Panel title="Project and submission details" subtitle="These fields stay with the project and are included in the repository package manifest.">
      <div className="metadata-form-grid">
        <label><span>Project name</span><input value={project?.name ?? ''} onChange={event => setProject(current => current ? { ...current, name: event.target.value } : current)} /></label>
        <label><span>Repository target</span><input value={metadata.repository} placeholder="PRIDE, MassIVE, Panorama Public" onChange={event => setMetadata(current => ({ ...current, repository: event.target.value }))} /></label>
        <label className="metadata-wide"><span>Description</span><textarea value={project?.description ?? ''} onChange={event => setProject(current => current ? { ...current, description: event.target.value } : current)} /></label>
        <label><span>Keywords, one per line</span><textarea value={metadata.keywords} onChange={event => setMetadata(current => ({ ...current, keywords: event.target.value }))} /></label>
        <label><span>Contacts, one per line</span><textarea value={metadata.contacts} onChange={event => setMetadata(current => ({ ...current, contacts: event.target.value }))} /></label>
        <label><span>Publications or DOIs</span><textarea value={metadata.publications} onChange={event => setMetadata(current => ({ ...current, publications: event.target.value }))} /></label>
        <label><span>Funding identifiers</span><textarea value={metadata.funding} onChange={event => setMetadata(current => ({ ...current, funding: event.target.value }))} /></label>
        <label><span>License</span><input value={metadata.license} placeholder="CC BY 4.0" onChange={event => setMetadata(current => ({ ...current, license: event.target.value }))} /></label>
        <label><span>Embargo date</span><input type="date" value={metadata.embargoDate} onChange={event => setMetadata(current => ({ ...current, embargoDate: event.target.value }))} /></label>
      </div>
    </Panel>

    <Panel title="SDRF metadata" subtitle="One row represents one sample linked to one assay and primary data file." className="sdrf-panel" actions={<div className="sdrf-actions">
      <input ref={uploadRef} type="file" accept=".tsv,.txt,text/tab-separated-values" hidden onChange={event => importFile(event.target.files?.[0])} />
      <button className="button button-secondary button-small" disabled={Boolean(busy)} onClick={() => uploadRef.current?.click()}><FileUp size={14} /> Import TSV</button>
      <button className="button button-secondary button-small" disabled={Boolean(busy)} onClick={() => void generate()}><RefreshCw size={14} /> Generate from project</button>
      {document && <button className="button button-primary button-small" disabled={Boolean(busy)} onClick={() => void saveSdrf()}><Save size={14} /> Save table</button>}
    </div>}>
      {!document ? <EmptyState title="No SDRF document yet" description="Generate one from current runs and extracted metadata, or import an existing SDRF TSV file." action="Generate SDRF" onAction={() => void generate()} /> : <>
        <div className="sdrf-summary">
          <span className={`sdrf-state sdrf-${document.status}`}>{document.status}</span>
          <span>Revision {document.revision}</span>
          <span>{document.rows.length} rows</span>
          <span>{document.columns.length} columns</span>
          <span>{document.specificationVersion}</span>
        </div>
        <div className="template-picker">
          <strong>Validation templates</strong>
          <div>{templates.map(template => {
            const value = `${template.name} ${template.version}`
            return <label key={value}><input type="checkbox" checked={document.templates.includes(value)} onChange={() => toggleTemplate(template)} />{template.name}<small>{template.kind}</small></label>
          })}</div>
        </div>
        <div className="sdrf-grid-wrap"><table className="sdrf-grid">
          <thead><tr><th className="sdrf-index">#</th>{document.columns.map((column, columnIndex) => <th key={`${column}-${columnIndex}`}><div><input aria-label={`Column ${columnIndex + 1}`} value={column} onChange={event => editColumn(columnIndex, event.target.value)} /><button className="icon-button" aria-label={`Remove column ${column}`} onClick={() => removeColumn(columnIndex)}><Trash2 size={13} /></button></div></th>)}<th className="sdrf-row-action"><button className="icon-button" aria-label="Add SDRF column" onClick={addColumn}><Plus size={15} /></button></th></tr></thead>
          <tbody>{document.rows.map((row, rowIndex) => <tr key={row.id ?? rowIndex}><td className="sdrf-index"><strong>{rowIndex + 1}</strong>{row.runId && <small title={row.runId}>mapped</small>}</td>{row.values.map((value, columnIndex) => <td key={columnIndex} className={validation?.messages.some(message => message.severity === 'error' && message.row === rowIndex && message.column === columnIndex) ? 'sdrf-cell-error' : ''}><input aria-label={`Row ${rowIndex + 1}, ${document.columns[columnIndex]}`} value={value} onChange={event => editCell(rowIndex, columnIndex, event.target.value)} /></td>)}<td className="sdrf-row-action"><button className="icon-button" aria-label={`Remove SDRF row ${rowIndex + 1}`} onClick={() => removeRow(rowIndex)}><Trash2 size={14} /></button></td></tr>)}</tbody>
        </table></div>
        <div className="sdrf-table-footer"><button className="button button-secondary button-small" onClick={addRow}><Plus size={14} /> Add row</button><span>Duplicate template and associated-file columns are preserved in order.</span></div>
      </>}
    </Panel>

    {document && <div className="sdrf-bottom-grid">
      <Panel title="Validation" subtitle="Run local structural checks and the official PSI SDRF validator.">
        <div className="validation-controls"><label><input type="checkbox" checked={ontology} onChange={event => setOntology(event.target.checked)} /> Validate ontology terms</label><button className="button button-primary" disabled={Boolean(busy)} onClick={() => void validate()}><FileCheck2 size={16} /> Validate now</button></div>
        {!validation ? <p className="panel-placeholder">This revision has not been validated.</p> : <div className="validation-result">
          <div className={validation.valid ? 'validation-pass' : 'validation-fail'}>{validation.valid ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}<strong>{validation.valid ? 'Valid SDRF' : `${validation.errorCount} validation errors`}</strong><span>{validation.warningCount} warnings, {validation.engine}</span></div>
          {validation.messages.length > 0 && <ul>{validation.messages.map((message, index) => <li key={`${message.code}-${index}`} className={`validation-${message.severity}`}><strong>{message.code}</strong><span>{message.message}</span>{message.row !== undefined && <small>Row {message.row + 1}{message.column !== undefined ? `, column ${message.column + 1}` : ''}</small>}</li>)}</ul>}
        </div>}
      </Panel>
      <Panel title="Repository package" subtitle="Download the SDRF, source files, derivatives, checksums, and project manifest.">
        <div className="submission-summary"><div><strong>{preview?.sourceCount ?? 0}</strong><span>source files</span></div><div><strong>{preview?.derivativeCount ?? 0}</strong><span>derived files</span></div><div><strong>{formatBytes(preview?.totalBytes ?? 0)}</strong><span>package data</span></div><div><strong>{preview?.mappedRows ?? 0}</strong><span>mapped rows</span></div></div>
        {preview && preview.unmappedRows > 0 && <p className="submission-warning"><AlertTriangle size={14} /> {preview.unmappedRows} SDRF rows are not mapped to stored primary files.</p>}
        <div className="submission-actions"><button className="button button-secondary" disabled={Boolean(busy)} onClick={() => void exportSdrf()}><Download size={16} /> Export SDRF</button><button className="button button-primary" disabled={!preview?.ready || Boolean(busy)} title={preview?.ready ? '' : 'Validate the SDRF and add at least one source file first'} onClick={() => void exportSubmission()}><FileArchive size={16} /> Download package</button></div>
      </Panel>
    </div>}
  </>
}
