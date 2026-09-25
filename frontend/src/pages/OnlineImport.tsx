import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { remoteApi, type RemoteImport, type RemoteSelection, type RepositoryDataset, type RepositoryFile, type RepositorySdrf } from '../api/remote'
import { formatBytes } from '../components/Data'
import { Panel } from '../components/Page'
import type { Experiment } from '../types'

function newKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
const message = (error: unknown) => error instanceof Error ? error.message : 'Repository request failed'

function defaultSelection(file: RepositoryFile): RemoteSelection {
  const name = file.filename.replace(/\.(raw|mzml|mzxml|mgf|ms2)(\.gz)?$/i, '').slice(0, 255)
  return { file_id: file.id, run_name: name, sample_name: name }
}

export function OnlineImport({ projectId, projectName, experimentName, experiments, onProjectName, onExperimentName }: {
  projectId: string, projectName: string, experimentName: string, experiments: Experiment[]
  onProjectName: (name: string) => void, onExperimentName: (name: string) => void
}) {
  const [accession, setAccession] = useState('')
  const [dataset, setDataset] = useState<RepositoryDataset | null>(null)
  const [selection, setSelection] = useState<Record<string, RemoteSelection>>({})
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [visibleLimit, setVisibleLimit] = useState(100)
  const [sdrf, setSdrf] = useState<RepositorySdrf | null>(null)
  const [sdrfId, setSdrfId] = useState('')
  const [includeSdrf, setIncludeSdrf] = useState(true)
  const [sdrfError, setSdrfError] = useState<string | null>(null)
  const [items, setItems] = useState<RemoteImport[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const attempt = useRef({ signature: '', key: '' })

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const result = await remoteApi.list(projectId || undefined)
        if (active) { setItems(result) }
        if (active) { setPollError(null) }
      } catch (reason) {
        if (active) setPollError(message(reason))
      } finally {
        if (active) timer = setTimeout(() => void refresh(), 2000)
      }
    }
    void refresh()
    return () => { active = false
      clearTimeout(timer)
    }
  }, [projectId])

  const loadSdrf = async (accession: string, fileId: string) => {
    setSdrfId(fileId)
    setSdrf(null)
    setSdrfError(null)
    try { setSdrf(await remoteApi.sdrf(accession, fileId)) }
    catch (reason) { setSdrfError(message(reason)) }
  }
  const lookup = async () => {
    setBusy(true)
    setError(null)
    setDataset(null)
    setSelection({})
    setSearch('')
    setFilter('all')
    setVisibleLimit(100)
    setSdrf(null)
    setSdrfId('')
    setSdrfError(null)
    setIncludeSdrf(true)
    try {
      const found = await remoteApi.lookup(accession, projectId || undefined)
      setDataset(found)
      const available = found.files.find(file => file.is_sdrf)
      if (available) await loadSdrf(found.accession, available.id)
    }
    catch (reason) { setError(message(reason)) }
    finally { setBusy(false) }
  }
  const enqueue = async () => {
    if (!dataset) return
    setBusy(true)
    setError(null)
    setNotice('')
    try {
      const destination = await api.prepareImport({ projectId: projectId || undefined, projectName, experimentName })
      const files = Object.values(selection)
      const selectedSdrf = includeSdrf && sdrf ? { file_id: sdrf.file_id, sha256: sdrf.sha256 } : undefined
      const signature = JSON.stringify([dataset.accession, destination.experimentId, files, selectedSdrf])
      if (attempt.current.signature !== signature) attempt.current = { signature, key: newKey() }
      const created = await remoteApi.enqueue(dataset.accession, destination.experimentId, files, attempt.current.key, selectedSdrf)
      setItems(current => [...created, ...current.filter(item => !created.some(row => row.id === item.id))])
      setSelection({})
      setNotice(`${created.length} ${created.length === 1 ? 'download' : 'downloads'} queued. You can close this page and return to Online repository to follow progress.`)
    } catch (reason) { setError(message(reason)) }
    finally { setBusy(false) }
  }
  const action = async (id: string, kind: 'cancel' | 'retry') => {
    setBusy(true)
    setError(null)
    try {
      const result = await remoteApi.action(id, kind)
      setItems(current => current.map(item => item.id === id ? result : item))
    } catch (reason) { setError(message(reason)) }
    finally { setBusy(false) }
  }
  const matchingFiles = dataset?.files.filter(file => file.filename.toLowerCase().includes(search.trim().toLowerCase()) &&
    (filter === 'all' || (filter === 'raw' ? file.category === 'RAW' : file.supported && file.category !== 'RAW'))) ?? []
  const files = matchingFiles.slice(0, visibleLimit)
  const selectableMatches = matchingFiles.filter(file => file.supported)
  const newMatches = selectableMatches.filter(file => !selection[file.id])
  const exceedsLimit = Object.keys(selection).length + newMatches.length > 500
  const atSelectionLimit = Object.keys(selection).length >= 500
  const hasMatchingSelection = matchingFiles.some(file => selection[file.id])
  const selectMatching = () => setSelection(current => {
    const next = { ...current }
    for (const file of selectableMatches) {
      if (!next[file.id] && Object.keys(next).length < 500) next[file.id] = defaultSelection(file)
    }
    return next
  })
  const clearMatching = () => setSelection(current => {
    const next = { ...current }
    for (const file of matchingFiles) delete next[file.id]
    return next
  })
  const selectedBytes = dataset?.files.reduce((total, file) => total + (selection[file.id] ? file.byte_size : 0), 0) ?? 0
  const sampleNames = (fileId: string) => {
    if (!includeSdrf || !sdrf) return []
    const column = sdrf.columns.findIndex(name => name.toLowerCase() === 'source name')
    return [...new Set((sdrf.mappings[fileId] ?? []).map(index => sdrf.rows[index][column]))]
  }
  const selectedSdrfRows = Object.keys(selection).reduce((count, id) => count + (sdrf?.mappings[id]?.length ?? 0), 0)
  const updateName = (id: string, key: 'run_name' | 'sample_name', value: string) => setSelection(current => ({ ...current, [id]: { ...current[id], [key]: value } }))

  return <div className="repository-import">
    {(error || pollError) && <div className="message-banner" role="alert">{error ?? pollError}</div>}
    {notice && <p role="status">{notice}</p>}
    <Panel title="Find a PRIDE dataset" subtitle="Download public acquisitions directly to this server.">
      <form onSubmit={event => { event.preventDefault()
        void lookup()
      }}>
        <div className="form-grid">
          <label><span>PRIDE accession or project URL</span><input required maxLength={2048} value={accession} onChange={event => setAccession(event.target.value)} placeholder="PXD000001" disabled={busy} /></label>
        </div>
        <div className="import-actions"><button type="submit" className="button button-secondary" disabled={busy || !accession.trim()}>{busy ? 'Working' : 'Look up dataset'}</button></div>
      </form>
    </Panel>
    {dataset && <form onSubmit={event => { event.preventDefault()
      void enqueue()
    }}>
      {(dataset.files.some(file => file.is_sdrf) || dataset.sdrf_warning) && <Panel title="SDRF sample metadata" subtitle="Matched by acquisition filename. Only rows for selected files are imported.">
        {dataset.sdrf_warning && <p className="repository-copy">{dataset.sdrf_warning}</p>}
        {dataset.files.some(file => file.is_sdrf) && <>
          <div className="form-grid"><label><span>SDRF file</span><select value={sdrfId} disabled={busy} onChange={event => {
            setBusy(true)
            void loadSdrf(dataset.accession, event.target.value).finally(() => setBusy(false))
          }}>{dataset.files.filter(file => file.is_sdrf).map(file => <option key={file.id} value={file.id}>{file.filename}</option>)}</select></label></div>
          {sdrfError && <p className="repository-copy" role="alert">{sdrfError} <button type="button" className="button button-secondary" disabled={busy} onClick={() => {
            setBusy(true)
            void loadSdrf(dataset.accession, sdrfId).finally(() => setBusy(false))
          }}>Retry SDRF preview</button></p>}
          {sdrf && <>
            <label className="repository-copy repository-checkbox"><input type="checkbox" checked={includeSdrf} disabled={busy} onChange={event => setIncludeSdrf(event.target.checked)} /> Import SDRF metadata for selected acquisitions</label>
            <p className="repository-copy">{selectedSdrfRows} matching rows for the current selection. Existing project SDRF rows and sample annotations are preserved. Files without a match use the sample name entered below.</p>
            {sdrf.warnings.map(warning => <p className="repository-copy" key={warning}>{warning}</p>)}
            <details className="repository-copy"><summary>Preview SDRF rows ({sdrf.rows.length} total)</summary><div className="table-scroll"><table><thead><tr>{sdrf.columns.map((column, index) => <th key={index}>{column}</th>)}</tr></thead><tbody>{sdrf.rows.slice(0, 10).map((row, index) => <tr key={index}>{row.map((value, column) => <td key={column}>{value}</td>)}</tr>)}</tbody></table></div>{sdrf.rows.length > 10 && <p>Showing the first 10 rows.</p>}</details>
          </>}
        </>}
      </Panel>}
      <Panel title={dataset.title} subtitle={dataset.accession}>
        <p className="repository-copy">{dataset.description}</p>
        <p className="repository-copy"><a href={dataset.url} target="_blank" rel="noreferrer">View dataset on PRIDE</a>{dataset.doi && ` · DOI: ${dataset.doi}`}{dataset.license && ` · ${dataset.license}`}</p>
        <div className="form-grid">
          <label><span>Project</span><input required maxLength={255} readOnly={Boolean(projectId)} disabled={busy} value={projectName} onChange={event => onProjectName(event.target.value)} /></label>
          <label><span>Experiment</span><input required maxLength={255} list="online-experiments" disabled={busy} value={experimentName} onChange={event => onExperimentName(event.target.value)} /><datalist id="online-experiments">{experiments.map(item => <option key={item.id} value={item.name} />)}</datalist></label>
          <label><span>File filter</span><select disabled={busy} value={filter} onChange={event => {
            setFilter(event.target.value)
            setVisibleLimit(100)
          }}><option value="all">All files</option><option value="raw">Raw data</option><option value="open">Supported open formats</option></select></label>
          <label><span>Search filenames</span><input type="search" disabled={busy} value={search} placeholder="Filter by part of a filename" onChange={event => {
            setSearch(event.target.value)
            setVisibleLimit(100)
          }} /></label>
        </div>
        <div className="import-actions">
          <button className="button button-secondary" type="button" disabled={busy || !newMatches.length || exceedsLimit} onClick={selectMatching}>Select matching files</button>
          <button className="button button-secondary" type="button" disabled={busy || !hasMatchingSelection} onClick={clearMatching}>Clear matching selection</button>
          <button className="button button-secondary" type="button" disabled={busy || !Object.keys(selection).length} onClick={() => setSelection({})}>Clear all selections</button>
        </div>
        <p className="repository-copy">Showing {files.length} of {matchingFiles.length} matching files, {selectableMatches.length} supported. Bulk selection includes all supported matches, including those not yet shown. Selections remain when filters change.</p>
        {exceedsLimit && <p className="repository-copy">A batch can contain up to 500 files. Refine the search or clear selections before selecting all matches.</p>}
        <p className="repository-copy">{Object.keys(selection).length} {Object.keys(selection).length === 1 ? 'file' : 'files'} selected, {formatBytes(selectedBytes)}. Server free space: {formatBytes(dataset.free_bytes)}. Up to {dataset.download_concurrency ?? 2} files download in parallel. Additional space is needed during import and conversion.</p>
        <div className="table-scroll"><table className="batch-import-table"><thead><tr><th>Select</th><th>File</th><th>Size</th><th>Run name</th><th>Sample</th></tr></thead>
          <tbody>{files.map(file => <tr key={file.id}>
            <td><input type="checkbox" aria-label={`Select ${file.filename}`} disabled={busy || !file.supported || !selection[file.id] && atSelectionLimit} checked={Boolean(selection[file.id])} onChange={event => {
              const checked = event.target.checked
              setSelection(current => {
                const next = { ...current }
                if (checked) {
                  next[file.id] = defaultSelection(file)
                } else delete next[file.id]
                return next
              })
            }} /></td>
            <td><strong>{file.filename}</strong><small>{file.category}{file.previously_imported ? ' · Previously imported into this project' : ''}</small>{file.reason && <small>{file.reason}</small>}</td>
            <td>{formatBytes(file.byte_size)}</td>
            <td>{selection[file.id] && <input required maxLength={255} disabled={busy} aria-label={`Run name for ${file.filename}`} value={selection[file.id].run_name} onChange={event => updateName(file.id, 'run_name', event.target.value)} />}</td>
            <td>{selection[file.id] && (sampleNames(file.id).length ? <><span>{sampleNames(file.id).join(', ')}</span><small>From SDRF ({sdrf?.mappings[file.id]?.length} rows)</small></> : <input required maxLength={255} disabled={busy} aria-label={`Sample for ${file.filename}`} value={selection[file.id].sample_name} onChange={event => updateName(file.id, 'sample_name', event.target.value)} />)}</td>
          </tr>)}</tbody></table></div>
        {files.length < matchingFiles.length && <div className="import-actions"><button className="button button-secondary" type="button" disabled={busy} onClick={() => setVisibleLimit(current => current + 100)}>Show 100 more files</button></div>}
        <p className="batch-import-note">Select supported single-file acquisitions. Archives, vendor bundles, and supporting documents are not imported here.</p>
        <div className="import-actions"><button className="button button-primary" type="submit" disabled={busy || !Object.keys(selection).length || Object.keys(selection).length > 500}>Download {Object.keys(selection).length} {Object.keys(selection).length === 1 ? 'file' : 'files'}</button></div>
      </Panel>
    </form>}
    <Panel title="Repository downloads" subtitle="Saved on the server. Downloads continue when you close the browser.">
      {items.length === 0 ? <p className="repository-copy">No repository downloads yet.</p> : <div className="table-scroll"><table className="batch-import-table"><thead><tr><th>Acquisition</th><th>Progress</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id}>
          <td><strong>{item.run_name}</strong><small>{item.accession} · {item.filename}</small></td>
          <td><progress aria-label={`Download progress for ${item.filename}`} max={item.byte_size || 1} value={item.bytes_received} /><small>{formatBytes(item.bytes_received)} / {formatBytes(item.byte_size)}</small></td>
          <td>{Boolean(item.sdrf_rows) && <small>{item.sdrf_rows} SDRF {item.sdrf_rows === 1 ? 'row' : 'rows'}{item.state === 'succeeded' ? ' imported' : ' pending'}</small>}{item.cancel_requested && !['succeeded', 'cancelled'].includes(item.state) ? 'Cancelling' : item.state}{item.error && <small className="batch-import-error">{item.error}</small>}</td>
          <td>{['failed', 'cancelled'].includes(item.state) ? <button type="button" className="button button-secondary" disabled={busy} onClick={() => void action(item.id, 'retry')}>Retry {item.run_name}</button>
            : item.state !== 'succeeded' && <button type="button" className="button button-secondary" disabled={busy || item.cancel_requested} onClick={() => void action(item.id, 'cancel')}>Cancel {item.run_name}</button>}
            {item.state === 'succeeded' && item.run_id && <Link className="button button-secondary" to={`/projects/${item.project_id}/runs/${item.run_id}`}>Open {item.run_name}</Link>}
          </td>
        </tr>)}</tbody></table></div>}
    </Panel>
  </div>
}
