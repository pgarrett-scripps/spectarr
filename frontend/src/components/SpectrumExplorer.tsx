import { ChevronLeft, ChevronRight, ListFilter, LoaderCircle, RotateCcw, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { MouseEvent } from 'react'
import { api } from '../api/client'
import type { Artifact, SpectrumCatalogPage, SpectrumSummary, SpxtacularSpectrum } from '../types'

type SpectrumLoader = typeof api.spectrum
type SpectrumCatalogLoader = typeof api.spectra
type SpectrumSelection = { index?: number, scanNumber?: number, nativeId?: string }
type FindMode = 'rt' | 'scan' | 'precursor' | 'native'

const supportedFormats = new Set(['RAW', 'mzML', 'MGF', 'MS2', 'MSP'])
const catalogPageSize = 12

export function SpectrumExplorer({ artifacts, preferredMsLevel, spectrumCounts, chromatogram = [], loadSpectrum = api.spectrum, loadCatalog = api.spectra }: { artifacts: Artifact[], preferredMsLevel?: 1 | 2, spectrumCounts?: Record<string, number>, chromatogram?: Array<{ time: number, intensity: number }>, loadSpectrum?: SpectrumLoader, loadCatalog?: SpectrumCatalogLoader }) {
  const candidates = useMemo(
    () => artifacts.filter(artifact => artifact.status === 'verified' && supportedFormats.has(artifact.format)),
    [artifacts]
  )
  const [artifactId, setArtifactId] = useState(() => preferredArtifact(candidates)?.id ?? '')
  const selectedArtifact = candidates.find(artifact => artifact.id === artifactId) ?? preferredArtifact(candidates)
  const [msLevel, setMsLevel] = useState<1 | 2>(() => defaultMsLevel(selectedArtifact, preferredMsLevel))
  const [selection, setSelection] = useState<SpectrumSelection>({ index: 0 })
  const [summary, setSummary] = useState<SpectrumSummary | null>(null)
  const [spectrum, setSpectrum] = useState<SpxtacularSpectrum | null>(null)
  const [loading, setLoading] = useState(false)
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalog, setCatalog] = useState<SpectrumCatalogPage | null>(null)
  const [browseOpen, setBrowseOpen] = useState(false)
  const [findMode, setFindMode] = useState<FindMode>('rt')
  const [findValue, setFindValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const knownCount = spectrumCounts?.[String(msLevel)]
  const currentIndex = summary?.index ?? selection.index ?? 0
  const maxIndex = knownCount === undefined ? Math.max(currentIndex + 1, catalog?.total ? catalog.total - 1 : 10_000_000) : Math.max(0, knownCount - 1)
  const ms1Unavailable = selectedArtifact?.format === 'MGF' || selectedArtifact?.format === 'MS2' || selectedArtifact?.format === 'MSP' || spectrumCounts?.['1'] === 0
  const ms2Unavailable = spectrumCounts?.['2'] === 0

  useEffect(() => {
    if (!selectedArtifact) return
    let active = true
    setLoading(true)
    setError(null)
    loadSpectrum(selectedArtifact.id, { msLevel, ...selection })
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
  }, [loadSpectrum, msLevel, refresh, selectedArtifact, selection])

  if (!selectedArtifact) {
    return <div className="settings-placeholder">No RAW, mzML, MGF, MS2, or MSP artifact is available for spectrum viewing.</div>
  }

  const selectArtifact = (nextId: string) => {
    const artifact = candidates.find(candidate => candidate.id === nextId)
    setArtifactId(nextId)
    setMsLevel(defaultMsLevel(artifact, preferredMsLevel))
    setSelection({ index: 0 })
    setSummary(null)
    setCatalog(null)
    setBrowseOpen(false)
  }

  const selectLevel = (level: 1 | 2) => {
    setMsLevel(level)
    setSelection({ index: 0 })
    setSummary(null)
    setCatalog(null)
    setBrowseOpen(false)
  }

  const fetchCatalog = async (query: { offset?: number, rtSeconds?: number, scanNumber?: number, nativeId?: string, precursorMz?: number } = {}) => {
    setCatalogLoading(true)
    setError(null)
    try {
      const value = await loadCatalog(selectedArtifact.id, { msLevel, limit: catalogPageSize, ...query })
      setCatalog(value)
      return value
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not browse spectra')
      return null
    } finally {
      setCatalogLoading(false)
    }
  }

  const chooseSummary = (item: SpectrumSummary) => {
    setSummary(item)
    setSelection(item.native_id ? { nativeId: item.native_id } : item.scan_number !== null ? { scanNumber: item.scan_number } : { index: item.index })
  }

  const stepSpectrum = async (direction: -1 | 1) => {
    const nextIndex = Math.min(maxIndex, Math.max(0, currentIndex + direction))
    setSummary(null)
    setSelection({ index: nextIndex })
    if (browseOpen) {
      const page = await fetchCatalog({ offset: Math.max(0, nextIndex - Math.floor(catalogPageSize / 2)) })
      const item = page?.items.find(candidate => candidate.index === nextIndex)
      if (item) setSummary(item)
    }
  }

  const findSpectrum = async () => {
    const value = findValue.trim()
    if (!value) return
    const numeric = Number(value)
    if (findMode !== 'native' && (!Number.isFinite(numeric) || numeric < 0)) {
      setError('Enter a nonnegative number for this search')
      return
    }
    if (findMode === 'native') {
      setSummary(null)
      setSelection({ nativeId: value })
      return
    }
    const page = await fetchCatalog(
      findMode === 'rt' ? { rtSeconds: numeric * 60 }
        : findMode === 'scan' ? { scanNumber: Math.floor(numeric) }
          : findMode === 'precursor' ? { precursorMz: numeric }
            : {}
    )
    const match = page?.items.find(item => item.index === page.match_index) ?? page?.items[0]
    if (match) {
      chooseSummary(match)
      return
    }
    if (page) setError('No matching spectrum was found')
  }

  const selectRetentionTime = async (minutes: number) => {
    const page = await fetchCatalog({ rtSeconds: minutes * 60 })
    const match = page?.items.find(item => item.index === page.match_index)
    if (match) chooseSummary(match)
    else if (page) setError('No spectra with retention times were found')
  }

  const toggleBrowse = async () => {
    const opening = !browseOpen
    setBrowseOpen(opening)
    if (opening && !catalog) await fetchCatalog({ offset: Math.max(0, currentIndex - Math.floor(catalogPageSize / 2)) })
  }

  const retentionMinutes = spectrum?.metadata.rt === null || spectrum?.metadata.rt === undefined ? null : spectrum.metadata.rt / 60

  return <div className="spectrum-explorer">
    <div className="spectrum-toolbar">
      <label><span>Artifact</span><select aria-label="Spectrum artifact" value={selectedArtifact.id} onChange={event => selectArtifact(event.target.value)}>{candidates.map(artifact => <option value={artifact.id} key={artifact.id}>{artifact.name} ({artifact.format})</option>)}</select></label>
      <fieldset><legend>MS level</legend><button aria-pressed={msLevel === 1} className={msLevel === 1 ? 'active' : ''} disabled={ms1Unavailable} type="button" onClick={() => selectLevel(1)}>MS1</button><button aria-pressed={msLevel === 2} className={msLevel === 2 ? 'active' : ''} disabled={ms2Unavailable} type="button" onClick={() => selectLevel(2)}>MS2</button></fieldset>
      <label className="spectrum-find"><span>Find spectrum</span><div><select aria-label="Spectrum search field" value={findMode} onChange={event => setFindMode(event.target.value as FindMode)}><option value="rt">Retention time</option><option value="scan">Scan number</option><option value="precursor">Precursor m/z</option><option value="native">Native ID</option></select><input aria-label="Spectrum search" value={findValue} placeholder={findPlaceholder(findMode)} onChange={event => setFindValue(event.target.value)} onKeyDown={event => {
        if (event.key === 'Enter') void findSpectrum()
      }} /><button type="button" aria-label="Find spectrum" disabled={catalogLoading} onClick={() => void findSpectrum()}>{catalogLoading ? <LoaderCircle className="spin" size={14} /> : <Search size={14} />}</button></div></label>
      <button className="spectrum-browse" type="button" aria-expanded={browseOpen} disabled={catalogLoading} onClick={() => void toggleBrowse()}><ListFilter size={14} />{browseOpen ? 'Hide list' : 'Browse spectra'}</button>
      <button className="spectrum-reload" type="button" aria-label="Reload spectrum" disabled={loading} onClick={() => {
        setRefresh(value => value + 1)
      }}>{loading ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}</button>
    </div>
    {error && <div className="spectrum-error" role="alert">{error}</div>}
    <SpectrumChromatogram points={chromatogram} retentionMinutes={retentionMinutes} loading={catalogLoading} onSelect={minutes => void selectRetentionTime(minutes)} />
    <div className="spectrum-selection-control">
      <button type="button" aria-label="Previous spectrum" disabled={currentIndex === 0 || loading} onClick={() => void stepSpectrum(-1)}><ChevronLeft size={15} /></button>
      <div><strong>{spectrumSelectionTitle(spectrum, currentIndex)}</strong><span>{spectrumSelectionSubtitle(spectrum)}</span></div>
      <button type="button" aria-label="Next spectrum" onClick={() => void stepSpectrum(1)} disabled={loading || currentIndex >= maxIndex}><ChevronRight size={15} /></button>
    </div>
    {browseOpen && <SpectrumBrowser page={catalog} loading={catalogLoading} selectedIndex={currentIndex} onSelect={chooseSummary} onPage={offset => void fetchCatalog({ offset })} />}
    {loading && !spectrum ? <div className="spectrum-loading"><LoaderCircle className="spin" size={18} /> Reading {selectedArtifact.format} spectrum</div> : null}
    {spectrum ? <><SpectrumPlot spectrum={spectrum} /><SpectrumFacts spectrum={spectrum} /></> : null}
  </div>
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

function SpectrumBrowser({ page, loading, selectedIndex, onSelect, onPage }: { page: SpectrumCatalogPage | null, loading: boolean, selectedIndex: number, onSelect: (item: SpectrumSummary) => void, onPage: (offset: number) => void }) {
  if (loading && !page) return <div className="spectrum-browser-loading"><LoaderCircle className="spin" size={16} /> Building the spectrum catalog</div>
  if (!page?.items.length) return <div className="spectrum-browser-loading">No spectra match this selection.</div>
  return <div className="spectrum-browser">
    <table><thead><tr><th>RT</th><th>Scan</th><th>Level</th><th>Precursor</th><th>Peaks</th></tr></thead><tbody>{page.items.map(item => <tr className={item.index === selectedIndex ? 'selected' : ''} key={item.index} onClick={() => onSelect(item)} onKeyDown={event => {
      if (event.key === 'Enter' || event.key === ' ') onSelect(item)
    }} role="button" tabIndex={0}><td>{item.rt === null ? 'Unknown' : `${(item.rt / 60).toFixed(2)} min`}</td><td>{item.scan_number ?? 'Unknown'}</td><td>MS{item.ms_level}</td><td>{item.precursor_mz === null ? 'None' : `${item.precursor_mz.toFixed(4)}${item.precursor_charge ? ` (${formatCharge(item.precursor_charge)})` : ''}`}</td><td>{item.peak_count.toLocaleString()}</td></tr>)}</tbody></table>
    <div className="spectrum-browser-footer"><span>{page.total.toLocaleString()} MS{page.items[0].ms_level} spectra</span><div><button type="button" onClick={() => onPage(Math.max(0, page.offset - page.limit))} disabled={loading || page.offset === 0}>Previous</button><button type="button" onClick={() => onPage(page.offset + page.limit)} disabled={loading || page.offset + page.items.length >= page.total}>Next</button></div></div>
  </div>
}

function findPlaceholder(mode: FindMode): string {
  if (mode === 'rt') return '31.42 min'
  if (mode === 'scan') return '1001'
  if (mode === 'precursor') return '622.31'
  return 'controllerType=0 scan=1001'
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

function preferredArtifact(artifacts: Artifact[]): Artifact | undefined {
  return artifacts.find(artifact => artifact.role === 'source') ?? artifacts.find(artifact => artifact.format === 'mzML') ?? artifacts[0]
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
