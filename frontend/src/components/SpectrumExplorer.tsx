import { ChevronDown, ChevronLeft, ChevronRight, Filter, LoaderCircle, RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { MouseEvent } from 'react'
import { api, ApiError } from '../api/client'
import type { Artifact, Job, SpectrumCatalogPage, SpectrumQueryRequest, SpectrumSummary, SpxtacularSpectrum } from '../types'

type SpectrumLoader = typeof api.spectrum
type SpectrumCatalogLoader = typeof api.spectra
type SpectrumQueryLoader = typeof api.querySpectra
type CatalogSpectrumLoader = typeof api.catalogSpectrum
type CatalogBuilder = typeof api.extractArtifact
type JobLoader = typeof api.job
type SpectrumSelection = { entryId?: string, index?: number, scanNumber?: number, nativeId?: string }
type SortField = NonNullable<SpectrumQueryRequest['sort']>

interface FilterDraft {
  ms1: boolean
  ms2: boolean
  scanMin: string
  scanMax: string
  rtMin: string
  rtMax: string
  precursorMin: string
  precursorMax: string
  charge: string
  peaksMin: string
  peaksMax: string
  ticMin: string
  neutralMassMin: string
  neutralMassMax: string
  basePeakMin: string
  basePeakMax: string
  nativeId: string
  polarity: string
  representation: string
}

const supportedFormats = new Set(['RAW', 'mzML', 'MGF', 'MS2', 'MSP'])
const catalogPageSize = 50

export function SpectrumExplorer({ artifacts, preferredMsLevel, spectrumCounts, chromatogram = [], loadSpectrum = api.spectrum, loadCatalog, loadQuery, loadCatalogSpectrum = api.catalogSpectrum, buildCatalog = api.extractArtifact, loadJob = api.job }: { artifacts: Artifact[], preferredMsLevel?: 1 | 2, spectrumCounts?: Record<string, number>, chromatogram?: Array<{ time: number, intensity: number }>, loadSpectrum?: SpectrumLoader, loadCatalog?: SpectrumCatalogLoader, loadQuery?: SpectrumQueryLoader, loadCatalogSpectrum?: CatalogSpectrumLoader, buildCatalog?: CatalogBuilder, loadJob?: JobLoader }) {
  const candidates = useMemo(
    () => artifacts.filter(artifact => artifact.status === 'verified' && supportedFormats.has(artifact.format)),
    [artifacts]
  )
  const [artifactId, setArtifactId] = useState(() => preferredArtifact(candidates)?.id ?? '')
  const selectedArtifact = candidates.find(artifact => artifact.id === artifactId) ?? preferredArtifact(candidates)
  const [selection, setSelection] = useState<SpectrumSelection>({ index: 0 })
  const [summary, setSummary] = useState<SpectrumSummary | null>(null)
  const [spectrum, setSpectrum] = useState<SpxtacularSpectrum | null>(null)
  const [loading, setLoading] = useState(false)
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalog, setCatalog] = useState<SpectrumCatalogPage | null>(null)
  const [filters, setFilters] = useState<FilterDraft>(() => initialFilters(selectedArtifact, preferredMsLevel))
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [sort, setSort] = useState<SortField>('retention_time_seconds')
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc')
  const [cursor, setCursor] = useState<string | undefined>()
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([])
  const [catalogMode, setCatalogMode] = useState<'checking' | 'persistent' | 'fallback' | 'unavailable'>('checking')
  const [catalogAction, setCatalogAction] = useState<'idle' | 'queuing' | 'queued' | 'running' | 'complete' | 'failed'>('idle')
  const [catalogJob, setCatalogJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const catalogJobId = catalogJob?.id
  const msLevel = summary?.ms_level === 1 ? 1 : summary?.ms_level === 2 ? 2 : defaultMsLevel(selectedArtifact, preferredMsLevel)
  const query = useMemo(() => buildSpectrumQuery(filters, sort, direction, cursor), [cursor, direction, filters, sort])

  useEffect(() => {
    if (!selectedArtifact) return
    let active = true
    setLoading(true)
    setError(null)
    const { entryId, ...legacySelection } = selection
    const request = entryId
      ? loadCatalogSpectrum(selectedArtifact.id, entryId)
      : loadSpectrum(selectedArtifact.id, { msLevel, ...legacySelection })
    request
      .then(value => {
        if (active) setSpectrum(value)
      })
      .catch(reason => {
        if (!active) return
        setSpectrum(null)
        setError(reason instanceof Error ? reason.message : 'Could not load this spectrum')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [loadCatalogSpectrum, loadSpectrum, msLevel, refresh, selectedArtifact, selection])

  useEffect(() => {
    if (!selectedArtifact) return
    let active = true
    const timer = window.setTimeout(() => {
      setCatalogLoading(true)
      setError(null)
      const queryLoader = loadQuery ?? (loadCatalog ? legacyQueryAdapter(loadCatalog) : api.querySpectra)
      queryLoader(selectedArtifact.id, query)
        .then(value => {
          if (!active) return
          setCatalog(value)
          setCatalogMode(value.strategy ?? (value.schema_version === 2 ? 'persistent' : 'fallback'))
          selectFirstCatalogItem(value, setSummary, setSelection, setSpectrum)
        })
        .catch(async reason => {
          if (!active) return
          if (!loadQuery && !loadCatalog && reason instanceof ApiError && reason.status === 409) {
            try {
              const level = query.msLevels?.[0] === 2 ? 2 : 1
              const value = await api.spectra(selectedArtifact.id, { msLevel: level, limit: catalogPageSize })
              if (active) {
                setCatalog({ ...value, strategy: 'fallback' })
                setCatalogMode('fallback')
                selectFirstCatalogItem(value, setSummary, setSelection, setSpectrum)
              }
              return
            } catch (fallbackReason) {
              reason = fallbackReason
            }
          }
          setCatalog(null)
          setCatalogMode('unavailable')
          setError(reason instanceof Error ? reason.message : 'Could not query spectra')
        })
        .finally(() => {
          if (active) setCatalogLoading(false)
        })
    }, 250)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [loadCatalog, loadQuery, query, refresh, selectedArtifact])

  useEffect(() => {
    if (!catalogJobId || !['queued', 'running'].includes(catalogAction)) return
    let active = true
    let timer: number | undefined
    const poll = async () => {
      try {
        const job = await loadJob(catalogJobId)
        if (!active) return
        setCatalogJob(job)
        if (job.status === 'complete') {
          setCatalogAction('complete')
          setRefresh(value => value + 1)
          return
        }
        if (job.status === 'failed') {
          setCatalogAction('failed')
          setError(job.detail || 'Catalog extraction failed')
          return
        }
        setCatalogAction(job.status)
        timer = window.setTimeout(() => void poll(), 1000)
      } catch (reason) {
        if (!active) return
        setCatalogAction('failed')
        setError(reason instanceof Error ? reason.message : 'Could not check catalog extraction status')
      }
    }
    void poll()
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [catalogAction, catalogJobId, loadJob])

  if (!selectedArtifact) {
    return <div className="settings-placeholder">No RAW, mzML, MGF, MS2, or MSP artifact is available for spectrum viewing.</div>
  }

  const selectArtifact = (nextId: string) => {
    const artifact = candidates.find(candidate => candidate.id === nextId)
    setArtifactId(nextId)
    setFilters(initialFilters(artifact, preferredMsLevel))
    setSelection({ index: 0 })
    setSummary(null)
    setCatalog(null)
    setCatalogMode('checking')
    setCursor(undefined)
    setCursorHistory([])
    setCatalogAction('idle')
    setCatalogJob(null)
    setError(null)
  }

  const changeFilters = (values: Partial<FilterDraft>) => {
    setFilters(current => ({ ...current, ...values }))
    setCursor(undefined)
    setCursorHistory([])
  }

  const chooseSummary = (item: SpectrumSummary) => {
    setSummary(item)
    setSelection(item.id ? { entryId: item.id } : item.native_id ? { nativeId: item.native_id } : item.scan_number !== null ? { scanNumber: item.scan_number } : { index: item.index })
  }

  const selectedRowIndex = catalog?.items.findIndex(item => sameSummary(item, summary)) ?? -1
  const stepSpectrum = (step: -1 | 1) => {
    if (!catalog?.items.length) return
    const next = selectedRowIndex < 0 ? 0 : selectedRowIndex + step
    if (next >= 0 && next < catalog.items.length) chooseSummary(catalog.items[next])
  }

  const nextPage = () => {
    if (!catalog?.next_cursor) return
    setCursorHistory(history => [...history, cursor])
    setCursor(catalog.next_cursor ?? undefined)
  }

  const previousPage = () => {
    const previous = cursorHistory[cursorHistory.length - 1]
    setCursorHistory(history => history.slice(0, -1))
    setCursor(previous)
  }

  const updateSort = (next: SortField) => {
    if (sort === next) setDirection(value => value === 'asc' ? 'desc' : 'asc')
    else {
      setSort(next)
      setDirection('asc')
    }
    setCursor(undefined)
    setCursorHistory([])
  }

  const retentionMinutes = spectrum?.metadata.rt === null || spectrum?.metadata.rt === undefined ? null : spectrum.metadata.rt / 60
  const activeFilterCount = countFilters(filters, selectedArtifact, preferredMsLevel)
  const rebuildCatalog = async () => {
    setCatalogAction('queuing')
    setError(null)
    try {
      const job = await buildCatalog(selectedArtifact.id, true)
      setCatalogJob(job)
      setCatalogAction(job.status === 'running' ? 'running' : job.status === 'complete' ? 'complete' : job.status === 'failed' ? 'failed' : 'queued')
      if (job.status === 'complete') setRefresh(value => value + 1)
      if (job.status === 'failed') setError(job.detail || 'Catalog extraction failed')
    } catch (reason) {
      setCatalogAction('failed')
      setError(reason instanceof Error ? reason.message : 'Could not queue catalog extraction')
    }
  }

  return <div className="spectrum-explorer">
    <div className="spectrum-toolbar">
      <label><span>Artifact</span><select aria-label="Spectrum artifact" value={selectedArtifact.id} onChange={event => selectArtifact(event.target.value)}>{candidates.map(artifact => <option value={artifact.id} key={artifact.id}>{artifact.name} ({artifact.format})</option>)}</select></label>
      <div className="spectrum-catalog-state"><span>Access</span><strong data-mode={catalogMode}>{catalogModeLabel(catalogMode)}</strong></div>
      <button className="spectrum-browse" type="button" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen(value => !value)}><Filter size={14} />Filters{activeFilterCount ? ` (${activeFilterCount})` : ''}<ChevronDown size={13} /></button>
      <button className="spectrum-reload" type="button" aria-label="Reload spectrum" disabled={loading} onClick={() => {
        setRefresh(value => value + 1)
        setCursor(value => value)
      }}>{loading ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}</button>
    </div>
    {error && <div className="spectrum-error" role="alert">{error}</div>}
    <SpectrumFilters filters={filters} advancedOpen={advancedOpen} ms1Unavailable={selectedArtifact.format === 'MGF' || selectedArtifact.format === 'MS2' || selectedArtifact.format === 'MSP' || spectrumCounts?.['1'] === 0} ms2Unavailable={spectrumCounts?.['2'] === 0} onChange={changeFilters} onClear={() => changeFilters(initialFilters(selectedArtifact, preferredMsLevel))} />
    {catalogMode === 'fallback' && <div className="spectrum-catalog-notice"><span>{catalogActionMessage(catalogAction, catalogJob)}</span><button type="button" disabled={['queuing', 'queued', 'running', 'complete'].includes(catalogAction)} onClick={() => void rebuildCatalog()}>{catalogActionButton(catalogAction)}</button></div>}
    <SpectrumTable page={catalog} loading={catalogLoading} selected={summary} sort={sort} direction={direction} onSort={updateSort} onSelect={chooseSummary} onPreviousPage={previousPage} onNextPage={nextPage} canPrevious={cursorHistory.length > 0} />
    <div className="spectrum-selection-control">
      <button type="button" aria-label="Previous spectrum" disabled={selectedRowIndex <= 0 || loading} onClick={() => stepSpectrum(-1)}><ChevronLeft size={15} /></button>
      <div><strong>{spectrumSelectionTitle(spectrum, summary?.index ?? selection.index ?? 0)}</strong><span>{spectrumSelectionSubtitle(spectrum)}</span></div>
      <button type="button" aria-label="Next spectrum" onClick={() => stepSpectrum(1)} disabled={loading || !catalog || selectedRowIndex >= catalog.items.length - 1}><ChevronRight size={15} /></button>
    </div>
    <SpectrumChromatogram points={chromatogram} retentionMinutes={retentionMinutes} loading={catalogLoading} onSelect={minutes => changeFilters({ rtMin: Math.max(0, minutes - 0.05).toFixed(2), rtMax: (minutes + 0.05).toFixed(2) })} />
    {loading && !spectrum ? <div className="spectrum-loading"><LoaderCircle className="spin" size={18} /> Reading {selectedArtifact.format} spectrum</div> : null}
    {spectrum ? <><SpectrumPlot spectrum={spectrum} /><SpectrumFacts spectrum={spectrum} /></> : null}
  </div>
}

function SpectrumFilters({ filters, advancedOpen, ms1Unavailable, ms2Unavailable, onChange, onClear }: { filters: FilterDraft, advancedOpen: boolean, ms1Unavailable: boolean, ms2Unavailable: boolean, onChange: (value: Partial<FilterDraft>) => void, onClear: () => void }) {
  return <div className={`spectrum-filters ${advancedOpen ? 'advanced' : ''}`}>
    <div className="spectrum-filter-level"><span>MS level</span><label><input type="checkbox" checked={filters.ms1} disabled={ms1Unavailable || (filters.ms1 && !filters.ms2)} onChange={event => onChange({ ms1: event.target.checked })} /> MS1</label><label><input type="checkbox" checked={filters.ms2} disabled={ms2Unavailable || (filters.ms2 && !filters.ms1)} onChange={event => onChange({ ms2: event.target.checked })} /> MS2</label></div>
    <RangeFilter label="Scan" min={filters.scanMin} max={filters.scanMax} onChange={(min, max) => onChange({ scanMin: min, scanMax: max })} />
    <RangeFilter label="RT (min)" min={filters.rtMin} max={filters.rtMax} step="0.01" onChange={(min, max) => onChange({ rtMin: min, rtMax: max })} />
    <RangeFilter label="Precursor m/z" min={filters.precursorMin} max={filters.precursorMax} step="0.0001" onChange={(min, max) => onChange({ precursorMin: min, precursorMax: max })} />
    <label><span>Charge</span><input aria-label="Precursor charge" value={filters.charge} placeholder="2, 3" onChange={event => onChange({ charge: event.target.value })} /></label>
    <RangeFilter label="Peaks" min={filters.peaksMin} max={filters.peaksMax} onChange={(min, max) => onChange({ peaksMin: min, peaksMax: max })} />
    <label><span>Min total intensity</span><input aria-label="Minimum total intensity" type="number" min="0" value={filters.ticMin} onChange={event => onChange({ ticMin: event.target.value })} /></label>
    {advancedOpen && <>
      <RangeFilter label="Neutral mass" min={filters.neutralMassMin} max={filters.neutralMassMax} step="0.0001" onChange={(min, max) => onChange({ neutralMassMin: min, neutralMassMax: max })} />
      <RangeFilter label="Base peak m/z" min={filters.basePeakMin} max={filters.basePeakMax} step="0.0001" onChange={(min, max) => onChange({ basePeakMin: min, basePeakMax: max })} />
      <label><span>Native ID contains</span><input aria-label="Native ID contains" value={filters.nativeId} onChange={event => onChange({ nativeId: event.target.value })} /></label>
      <label><span>Polarity</span><select aria-label="Polarity" value={filters.polarity} onChange={event => onChange({ polarity: event.target.value })}><option value="">Any</option><option value="positive">Positive</option><option value="negative">Negative</option></select></label>
      <label><span>Representation</span><select aria-label="Representation" value={filters.representation} onChange={event => onChange({ representation: event.target.value })}><option value="">Any</option><option value="centroid">Centroid</option><option value="profile">Profile</option></select></label>
    </>}
    <button className="spectrum-clear-filters" type="button" onClick={onClear}><X size={13} />Clear</button>
  </div>
}

function RangeFilter({ label, min, max, step = '1', onChange }: { label: string, min: string, max: string, step?: string, onChange: (min: string, max: string) => void }) {
  return <label className="spectrum-range-filter"><span>{label}</span><div><input aria-label={`${label} minimum`} type="number" min="0" step={step} placeholder="Min" value={min} onChange={event => onChange(event.target.value, max)} /><input aria-label={`${label} maximum`} type="number" min="0" step={step} placeholder="Max" value={max} onChange={event => onChange(min, event.target.value)} /></div></label>
}

function SpectrumTable({ page, loading, selected, sort, direction, onSort, onSelect, onPreviousPage, onNextPage, canPrevious }: { page: SpectrumCatalogPage | null, loading: boolean, selected: SpectrumSummary | null, sort: SortField, direction: 'asc' | 'desc', onSort: (field: SortField) => void, onSelect: (item: SpectrumSummary) => void, onPreviousPage: () => void, onNextPage: () => void, canPrevious: boolean }) {
  if (loading && !page) return <div className="spectrum-browser-loading"><LoaderCircle className="spin" size={16} /> Querying spectrum catalog</div>
  return <div className="spectrum-browser spectrum-table-primary">
    <div className="spectrum-table-scroll"><table><thead><tr>
      <SortableHeader label="RT" field="retention_time_seconds" sort={sort} direction={direction} onSort={onSort} />
      <SortableHeader label="Scan" field="scan_number" sort={sort} direction={direction} onSort={onSort} />
      <SortableHeader label="Level" field="ms_level" sort={sort} direction={direction} onSort={onSort} />
      <SortableHeader label="Precursor m/z" field="precursor_mz" sort={sort} direction={direction} onSort={onSort} />
      <th>Charge</th><SortableHeader label="Peaks" field="peak_count" sort={sort} direction={direction} onSort={onSort} />
      <SortableHeader label="Total intensity" field="total_ion_current" sort={sort} direction={direction} onSort={onSort} />
      <SortableHeader label="Base peak m/z" field="base_peak_mz" sort={sort} direction={direction} onSort={onSort} />
    </tr></thead><tbody>{page?.items.map((item, itemIndex) => <tr className={sameSummary(item, selected) ? 'selected' : ''} key={item.id ?? `${item.ms_level}:${item.index}`} onClick={() => onSelect(item)} onKeyDown={event => {
      if (event.key === 'Enter' || event.key === ' ') onSelect(item)
      if (event.key === 'ArrowDown' && page.items[itemIndex + 1]) {
        event.preventDefault()
        onSelect(page.items[itemIndex + 1])
        const nextRow = event.currentTarget.nextElementSibling as HTMLElement | null
        nextRow?.focus()
      }
      if (event.key === 'ArrowUp' && page.items[itemIndex - 1]) {
        event.preventDefault()
        onSelect(page.items[itemIndex - 1])
        const previousRow = event.currentTarget.previousElementSibling as HTMLElement | null
        previousRow?.focus()
      }
    }} role="button" tabIndex={0}><td>{item.rt === null ? 'Unknown' : `${(item.rt / 60).toFixed(2)} min`}</td><td>{item.scan_number ?? 'Unknown'}</td><td>MS{item.ms_level}</td><td>{formatNumber(item.precursor_mz, 4)}</td><td>{item.precursor_charge ? formatCharge(item.precursor_charge, item.polarity) : 'None'}</td><td>{item.peak_count?.toLocaleString() ?? 'Unknown'}</td><td>{formatCompact(item.total_ion_current)}</td><td>{formatNumber(item.base_peak_mz, 4)}</td></tr>)}</tbody></table></div>
    {!loading && !page?.items.length && <div className="spectrum-browser-loading">No spectra match these filters.</div>}
    <div className="spectrum-browser-footer"><span>{page ? `${page.total.toLocaleString()} matching spectra` : 'Waiting for catalog'}{loading ? ' · Updating' : ''}</span><div><button type="button" onClick={onPreviousPage} disabled={loading || !canPrevious}>Previous 50</button><button type="button" onClick={onNextPage} disabled={loading || !page?.next_cursor}>Next 50</button></div></div>
  </div>
}

function SortableHeader({ label, field, sort, direction, onSort }: { label: string, field: SortField, sort: SortField, direction: 'asc' | 'desc', onSort: (field: SortField) => void }) {
  return <th><button type="button" onClick={() => onSort(field)}>{label}{sort === field ? direction === 'asc' ? ' ↑' : ' ↓' : ''}</button></th>
}

function selectFirstCatalogItem(page: SpectrumCatalogPage, setSummary: (value: SpectrumSummary | null) => void, setSelection: (value: SpectrumSelection) => void, setSpectrum: (value: SpxtacularSpectrum | null) => void) {
  const first = page.items[0]
  if (!first) {
    setSummary(null)
    setSpectrum(null)
    return
  }
  setSummary(first)
  setSelection(first.id ? { entryId: first.id } : first.native_id ? { nativeId: first.native_id } : first.scan_number !== null ? { scanNumber: first.scan_number } : { index: first.index })
}

function SpectrumChromatogram({ points, retentionMinutes, loading, onSelect }: { points: Array<{ time: number, intensity: number }>, retentionMinutes: number | null, loading: boolean, onSelect: (minutes: number) => void }) {
  if (points.length < 2) return <div className="spectrum-chromatogram-empty">No chromatogram preview is available. Use search or browse to select a spectrum.</div>
  const width = 800
  const height = 112
  const left = 8
  const right = 8
  const top = 12
  const bottom = 22
  const minTime = Math.min(...points.map(point => point.time))
  const maxTime = Math.max(...points.map(point => point.time))
  const maxIntensity = Math.max(...points.map(point => point.intensity), 1)
  const x = (time: number) => left + ((time - minTime) / Math.max(maxTime - minTime, 1)) * (width - left - right)
  const y = (intensity: number) => height - bottom - (intensity / maxIntensity) * (height - top - bottom)
  const coordinates = points.map(point => `${x(point.time).toFixed(1)},${y(point.intensity).toFixed(1)}`).join(' ')
  const cursor = retentionMinutes === null ? null : Math.min(width - right, Math.max(left, x(retentionMinutes)))
  const select = (event: MouseEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / Math.max(bounds.width, 1)))
    onSelect(minTime + ratio * (maxTime - minTime))
  }
  return <div className="spectrum-chromatogram-wrap">
    <div className="spectrum-chromatogram-heading"><strong>Total ion chromatogram</strong><span>{loading ? 'Preparing spectrum catalog' : cursor === null ? 'Click to select the nearest spectrum' : `Selected ${retentionMinutes?.toFixed(2)} min`}</span></div>
    <svg className="spectrum-chromatogram" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Clickable total ion chromatogram" onClick={select}>
      <g className="spectrum-grid">{[0.25, 0.5, 0.75].map(ratio => <line key={ratio} x1={left} x2={width - right} y1={top + ratio * (height - top - bottom)} y2={top + ratio * (height - top - bottom)} />)}</g>
      <polyline className="spectrum-chromatogram-line" points={coordinates} />
      {cursor !== null && <line className="spectrum-chromatogram-cursor" x1={cursor} x2={cursor} y1={top} y2={height - bottom} />}
      <text className="spectrum-axis-label" x={left} y={height - 5}>{minTime.toFixed(1)} min</text>
      <text className="spectrum-axis-label" textAnchor="middle" x={width / 2} y={height - 5}>Retention time</text>
      <text className="spectrum-axis-label" textAnchor="end" x={width - right} y={height - 5}>{maxTime.toFixed(1)} min</text>
    </svg>
  </div>
}

function initialFilters(artifact?: Artifact, preferredMsLevel?: 1 | 2): FilterDraft {
  const level = defaultMsLevel(artifact, preferredMsLevel)
  return { ms1: level === 1, ms2: level === 2, scanMin: '', scanMax: '', rtMin: '', rtMax: '', precursorMin: '', precursorMax: '', charge: '', peaksMin: '', peaksMax: '', ticMin: '', neutralMassMin: '', neutralMassMax: '', basePeakMin: '', basePeakMax: '', nativeId: '', polarity: '', representation: '' }
}

function buildSpectrumQuery(filters: FilterDraft, sort: SortField, direction: 'asc' | 'desc', cursor?: string): SpectrumQueryRequest {
  return {
    msLevels: [filters.ms1 ? 1 : null, filters.ms2 ? 2 : null].filter((value): value is number => value !== null),
    scanNumberMin: numeric(filters.scanMin), scanNumberMax: numeric(filters.scanMax),
    retentionTimeMin: seconds(filters.rtMin), retentionTimeMax: seconds(filters.rtMax),
    precursorMzMin: numeric(filters.precursorMin), precursorMzMax: numeric(filters.precursorMax),
    neutralMassMin: numeric(filters.neutralMassMin), neutralMassMax: numeric(filters.neutralMassMax),
    charges: filters.charge.split(/[ ,]+/).flatMap(value => value && Number.isInteger(Number(value)) ? [Number(value)] : []),
    peakCountMin: numeric(filters.peaksMin), peakCountMax: numeric(filters.peaksMax),
    totalIonCurrentMin: numeric(filters.ticMin),
    basePeakMzMin: numeric(filters.basePeakMin), basePeakMzMax: numeric(filters.basePeakMax),
    nativeId: filters.nativeId.trim() || undefined,
    polarities: filters.polarity ? [filters.polarity] : [],
    representations: filters.representation ? [filters.representation] : [],
    sort, direction, cursor, limit: catalogPageSize
  }
}

function legacyQueryAdapter(loader: SpectrumCatalogLoader): SpectrumQueryLoader {
  return (artifactId, query) => loader(artifactId, { msLevel: query.msLevels?.[0] === 2 ? 2 : 1, limit: query.limit })
}

function numeric(value: string): number | undefined {
  const number = Number(value)
  return value.trim() && Number.isFinite(number) && number >= 0 ? number : undefined
}

function seconds(minutes: string): number | undefined {
  const value = numeric(minutes)
  return value === undefined ? undefined : value * 60
}

function countFilters(filters: FilterDraft, artifact?: Artifact, preferredMsLevel?: 1 | 2): number {
  const defaults = initialFilters(artifact, preferredMsLevel)
  return Object.keys(filters).filter(key => filters[key as keyof FilterDraft] !== defaults[key as keyof FilterDraft]).length
}

function sameSummary(left: SpectrumSummary, right: SpectrumSummary | null): boolean {
  if (!right) return false
  return left.id && right.id ? left.id === right.id : left.ms_level === right.ms_level && left.index === right.index
}

function formatNumber(value: number | null | undefined, digits: number): string {
  return value === null || value === undefined ? 'None' : value.toFixed(digits)
}

function formatCompact(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Unknown' : Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

function spectrumSelectionTitle(spectrum: SpxtacularSpectrum | null, index: number): string {
  if (!spectrum) return `Spectrum ${index + 1}`
  const metadata = spectrum.metadata
  const parts = [metadata.rt === null || metadata.rt === undefined ? null : `${(metadata.rt / 60).toFixed(2)} min`, metadata.scan_number === null || metadata.scan_number === undefined ? null : `scan ${metadata.scan_number}`, metadata.ms_level ? `MS${metadata.ms_level}` : null].filter(Boolean)
  return parts.join(' · ') || `Spectrum ${index + 1}`
}

function spectrumSelectionSubtitle(spectrum: SpxtacularSpectrum | null): string {
  const precursor = spectrum?.metadata.precursors?.[0]
  if (!precursor) return spectrum?.metadata.native_id ?? 'No precursor information'
  return `Precursor ${precursor.mz.toFixed(4)} m/z${precursor.charge ? ` · ${formatCharge(precursor.charge, spectrum?.metadata.polarity)}` : ''}`
}

function catalogActionMessage(action: 'idle' | 'queuing' | 'queued' | 'running' | 'complete' | 'failed', job: Job | null): string {
  if (action === 'queuing') return 'Submitting the catalog extraction job.'
  if (action === 'queued') return 'Catalog extraction is queued and waiting for a worker.'
  if (action === 'running') return `Catalog extraction is running${job?.progress ? ` (${job.progress}%)` : ''}.`
  if (action === 'complete') return 'Catalog extraction completed. Loading the indexed catalog.'
  if (action === 'failed') return `Catalog extraction failed${job?.detail ? `: ${job.detail}` : ''}. Choose another artifact or retry.`
  return 'This artifact has no persistent catalog yet. Basic browsing is available, but advanced filters require extraction.'
}

function catalogModeLabel(mode: 'checking' | 'persistent' | 'fallback' | 'unavailable'): string {
  if (mode === 'persistent') return 'Indexed catalog'
  if (mode === 'fallback') return 'Compatibility mode'
  if (mode === 'unavailable') return 'Catalog unavailable'
  return 'Checking catalog'
}

function catalogActionButton(action: 'idle' | 'queuing' | 'queued' | 'running' | 'complete' | 'failed'): string {
  if (action === 'queuing') return 'Queuing'
  if (action === 'queued') return 'Queued'
  if (action === 'running') return 'Building'
  if (action === 'complete') return 'Loading'
  if (action === 'failed') return 'Retry catalog'
  return 'Build catalog'
}

function preferredArtifact(artifacts: Artifact[]): Artifact | undefined {
  const source = artifacts.find(artifact => artifact.role === 'source')
  if (source && source.format !== 'RAW') return source
  return artifacts.find(artifact => artifact.format === 'mzML')
    ?? artifacts.find(artifact => ['MGF', 'MS2', 'MSP'].includes(artifact.format))
    ?? source
    ?? artifacts[0]
}

function defaultMsLevel(artifact?: Artifact, preferredMsLevel?: 1 | 2): 1 | 2 {
  if (artifact?.format === 'MGF' || artifact?.format === 'MS2' || artifact?.format === 'MSP') return 2
  return preferredMsLevel ?? 1
}

export function SpectrumPlot({ spectrum }: { spectrum: SpxtacularSpectrum }) {
  const [hovered, setHovered] = useState<PlotPoint | null>(null)
  const points = useMemo(() => plotPoints(spectrum), [spectrum])
  if (!points.length) return <div className="settings-placeholder">This spectrum contains no peaks.</div>
  const width = 820
  const height = 270
  const left = 54
  const right = 16
  const top = 18
  const bottom = 35
  const plotWidth = width - left - right
  const baseline = height - bottom
  const plotHeight = baseline - top
  const minMz = points[0].mz
  const maxMz = points[points.length - 1].mz
  const maxIntensity = Math.max(...points.map(point => point.intensity), 1)
  const x = (mz: number) => left + ((mz - minMz) / Math.max(maxMz - minMz, 1)) * plotWidth
  const y = (intensity: number) => baseline - (intensity / maxIntensity) * plotHeight
  const profile = spectrum.metadata.spectrum_type === 'profile'
  const profileCoordinates = profile ? points.map(point => `${x(point.mz).toFixed(2)},${y(point.intensity).toFixed(2)}`).join(' ') : ''

  const hover = (event: MouseEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect()
    const localX = ((event.clientX - bounds.left) / bounds.width) * width
    const targetMz = minMz + ((localX - left) / plotWidth) * (maxMz - minMz)
    setHovered(nearestPoint(points, targetMz))
  }

  return <div className="spectrum-chart-wrap">
    <svg className="spectrum-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${spectrum.metadata.spectrum_type ?? 'mass'} spectrum`} onMouseMove={hover} onMouseLeave={() => setHovered(null)}>
      <g className="spectrum-grid">{[0.25, 0.5, 0.75, 1].map(ratio => <line key={ratio} x1={left} x2={width - right} y1={baseline - ratio * plotHeight} y2={baseline - ratio * plotHeight} />)}</g>
      <line className="spectrum-axis-line" x1={left} x2={width - right} y1={baseline} y2={baseline} />
      {profile
        ? <polyline className="spectrum-profile" points={profileCoordinates} />
        : <g className="spectrum-sticks">{points.map(point => <line key={point.key} x1={x(point.mz)} x2={x(point.mz)} y1={baseline} y2={y(point.intensity)} />)}</g>}
      {hovered && <g className="spectrum-hover-marker"><line x1={x(hovered.mz)} x2={x(hovered.mz)} y1={top} y2={baseline} /><circle cx={x(hovered.mz)} cy={y(hovered.intensity)} r="3" /></g>}
      <text className="spectrum-axis-label" x={left} y={height - 9}>{minMz.toFixed(2)}</text>
      <text className="spectrum-axis-label" textAnchor="middle" x={left + plotWidth / 2} y={height - 9}>m/z</text>
      <text className="spectrum-axis-label" textAnchor="end" x={width - right} y={height - 9}>{maxMz.toFixed(2)}</text>
      <text className="spectrum-axis-label" x="7" y={top + 4}>100%</text>
    </svg>
    <div className="spectrum-hover-value" role="status">{hovered ? <><strong>m/z {hovered.mz.toFixed(5)}</strong><span>{hovered.intensity.toLocaleString()} intensity</span>{hovered.charge !== undefined && <span>{formatCharge(hovered.charge, spectrum.metadata.polarity)} charge</span>}{hovered.im !== undefined && <span>{hovered.im.toFixed(5)} {spectrum.metadata.im_type ?? 'ion mobility'}</span>}{hovered.isoScore !== undefined && <span>{hovered.isoScore.toFixed(4)} isotope score</span>}</> : <span>Hover over the spectrum to inspect a peak</span>}</div>
  </div>
}

interface PlotPoint {
  key: number
  mz: number
  intensity: number
  charge?: number
  im?: number
  isoScore?: number
}

function plotPoints(spectrum: SpxtacularSpectrum): PlotPoint[] {
  const points = spectrum.arrays.mz.flatMap((mz, index) => {
    const intensity = spectrum.arrays.intensity[index]
    return Number.isFinite(mz) && Number.isFinite(intensity) ? [{
      key: index,
      mz,
      intensity,
      charge: finiteValue(spectrum.arrays.charge?.[index]),
      im: finiteValue(spectrum.arrays.im?.[index]),
      isoScore: finiteValue(spectrum.arrays.iso_score?.[index])
    }] : []
  })
  const sorted = points.sort((left, right) => left.mz - right.mz)
  if (sorted.length <= 2000) return sorted
  if (spectrum.metadata.spectrum_type === 'profile') {
    return decimateProfile(sorted, 2000)
  }
  return [...sorted].sort((left, right) => right.intensity - left.intensity).slice(0, 2000).sort((left, right) => left.mz - right.mz)
}

function decimateProfile(points: PlotPoint[], maxPoints: number): PlotPoint[] {
  if (points.length <= maxPoints) return points
  const buckets = Math.max(1, Math.floor((maxPoints - 2) / 2))
  const selected = new Map<number, PlotPoint>([[points[0].key, points[0]], [points.at(-1)!.key, points.at(-1)!]])
  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const start = Math.floor((bucket * points.length) / buckets)
    const end = Math.max(start + 1, Math.floor(((bucket + 1) * points.length) / buckets))
    let minimum = points[start]
    let maximum = points[start]
    for (let position = start + 1; position < end; position += 1) {
      if (points[position].intensity < minimum.intensity) minimum = points[position]
      if (points[position].intensity > maximum.intensity) maximum = points[position]
    }
    selected.set(minimum.key, minimum)
    selected.set(maximum.key, maximum)
  }
  return Array.from(selected.values()).sort((left, right) => left.mz - right.mz)
}

function nearestPoint(points: PlotPoint[], mz: number): PlotPoint {
  let low = 0
  let high = points.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (points[middle].mz < mz) low = middle + 1
    else high = middle
  }
  if (low === 0) return points[0]
  const previous = points[low - 1]
  return Math.abs(previous.mz - mz) <= Math.abs(points[low].mz - mz) ? previous : points[low]
}

function SpectrumFacts({ spectrum }: { spectrum: SpxtacularSpectrum }) {
  const metadata = spectrum.metadata
  const precursor = metadata.precursors?.[0]
  const rt = metadata.rt === null || metadata.rt === undefined ? 'Unknown' : `${(metadata.rt / 60).toFixed(2)} min`
  const facts = [
    ['Retention time', rt],
    ['Scan', String(metadata.scan_number ?? 'Unknown')],
    ['MS level', metadata.ms_level ? `MS${metadata.ms_level}` : 'Unknown'],
    ['Representation', metadata.spectrum_type ?? 'Unknown'],
    ['Peaks', spectrum.arrays.mz.length.toLocaleString()],
    ['Precursor', precursor ? `${precursor.mz.toFixed(4)}${precursor.charge ? ` (${formatCharge(precursor.charge, metadata.polarity)})` : ''}${metadata.precursors && metadata.precursors.length > 1 ? ` +${metadata.precursors.length - 1}` : ''}` : 'None'],
    ['Native ID', metadata.native_id ?? 'Unknown'],
    metadata.polarity ? ['Polarity', metadata.polarity] : null,
    metadata.analyzer ? ['Analyzer', metadata.analyzer] : null,
    metadata.activation_type ? ['Activation', metadata.activation_type] : null,
    metadata.collision_energy !== null && metadata.collision_energy !== undefined ? ['Collision energy', metadata.collision_energy.toLocaleString()] : null,
    metadata.injection_time !== null && metadata.injection_time !== undefined ? ['Injection time', `${metadata.injection_time.toLocaleString()} ms`] : null,
    metadata.resolution !== null && metadata.resolution !== undefined ? ['Resolution', metadata.resolution.toLocaleString()] : null,
    metadata.isolation_mz_range ? ['Isolation m/z', `${metadata.isolation_mz_range[0].toFixed(4)} to ${metadata.isolation_mz_range[1].toFixed(4)}`] : null,
    metadata.im_range ? ['Ion mobility', `${metadata.im_range[0].toFixed(4)} to ${metadata.im_range[1].toFixed(4)} ${metadata.im_type ?? ''}`.trim()] : null
  ].filter((fact): fact is string[] => fact !== null)
  return <div className="spectrum-facts">{facts.map(([label, value]) => <div title={value} key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
}

function formatCharge(charge: number, polarity?: string | null): string {
  return charge < 0 || polarity === 'negative' ? `${Math.abs(charge)}-` : `${charge}+`
}

function finiteValue(value: number | null | undefined): number | undefined {
  return value !== null && value !== undefined && Number.isFinite(value) ? value : undefined
}
