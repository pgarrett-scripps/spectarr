import { ChevronLeft, ChevronRight, LoaderCircle, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { MouseEvent } from 'react'
import { api } from '../api/client'
import type { Artifact, SpxtacularSpectrum } from '../types'

type SpectrumLoader = typeof api.spectrum

const supportedFormats = new Set(['RAW', 'mzML', 'MGF', 'MS2', 'MSP'])

export function SpectrumExplorer({ artifacts, preferredMsLevel, spectrumCounts, loadSpectrum = api.spectrum }: { artifacts: Artifact[], preferredMsLevel?: 1 | 2, spectrumCounts?: Record<string, number>, loadSpectrum?: SpectrumLoader }) {
  const candidates = useMemo(
    () => artifacts.filter(artifact => artifact.status === 'verified' && supportedFormats.has(artifact.format)),
    [artifacts]
  )
  const [artifactId, setArtifactId] = useState(() => preferredArtifact(candidates)?.id ?? '')
  const selectedArtifact = candidates.find(artifact => artifact.id === artifactId) ?? preferredArtifact(candidates)
  const [msLevel, setMsLevel] = useState<1 | 2>(() => defaultMsLevel(selectedArtifact, preferredMsLevel))
  const [index, setIndex] = useState(0)
  const [spectrum, setSpectrum] = useState<SpxtacularSpectrum | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const knownCount = spectrumCounts?.[String(msLevel)]
  const maxIndex = knownCount === undefined ? 10_000_000 : Math.max(0, knownCount - 1)
  const ms1Unavailable = selectedArtifact?.format === 'MGF' || selectedArtifact?.format === 'MS2' || selectedArtifact?.format === 'MSP' || spectrumCounts?.['1'] === 0
  const ms2Unavailable = spectrumCounts?.['2'] === 0

  useEffect(() => {
    if (!selectedArtifact) return
    let active = true
    setLoading(true)
    setError(null)
    loadSpectrum(selectedArtifact.id, { msLevel, index })
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
  }, [index, loadSpectrum, msLevel, refresh, selectedArtifact])

  if (!selectedArtifact) {
    return <div className="settings-placeholder">No RAW, mzML, MGF, MS2, or MSP artifact is available for spectrum viewing.</div>
  }

  const selectArtifact = (nextId: string) => {
    const artifact = candidates.find(candidate => candidate.id === nextId)
    setArtifactId(nextId)
    setMsLevel(defaultMsLevel(artifact, preferredMsLevel))
    setIndex(0)
  }

  const selectLevel = (level: 1 | 2) => {
    setMsLevel(level)
    setIndex(0)
  }

  return <div className="spectrum-explorer">
    <div className="spectrum-toolbar">
      <label><span>Artifact</span><select aria-label="Spectrum artifact" value={selectedArtifact.id} onChange={event => selectArtifact(event.target.value)}>{candidates.map(artifact => <option value={artifact.id} key={artifact.id}>{artifact.name} ({artifact.format})</option>)}</select></label>
      <fieldset><legend>MS level</legend><button aria-pressed={msLevel === 1} className={msLevel === 1 ? 'active' : ''} disabled={ms1Unavailable} type="button" onClick={() => selectLevel(1)}>MS1</button><button aria-pressed={msLevel === 2} className={msLevel === 2 ? 'active' : ''} disabled={ms2Unavailable} type="button" onClick={() => selectLevel(2)}>MS2</button></fieldset>
      <div className="spectrum-index-control">
        <button type="button" aria-label="Previous spectrum" disabled={index === 0 || loading} onClick={() => setIndex(value => Math.max(0, value - 1))}><ChevronLeft size={15} /></button>
        <label><span>Position</span><input aria-label="Spectrum position" min="0" max={maxIndex} type="number" value={index} onChange={event => setIndex(Math.min(maxIndex, Math.max(0, Math.floor(Number(event.target.value) || 0))))} /></label>
        <button type="button" aria-label="Next spectrum" onClick={() => setIndex(value => Math.min(maxIndex, value + 1))} disabled={loading || index >= maxIndex}><ChevronRight size={15} /></button>
      </div>
      <button className="spectrum-reload" type="button" aria-label="Reload spectrum" disabled={loading} onClick={() => {
        setRefresh(value => value + 1)
      }}>{loading ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}</button>
    </div>
    {error && <div className="spectrum-error" role="alert">{error}</div>}
    {loading && !spectrum ? <div className="spectrum-loading"><LoaderCircle className="spin" size={18} /> Reading {selectedArtifact.format} spectrum</div> : null}
    {spectrum ? <><SpectrumPlot spectrum={spectrum} /><SpectrumFacts spectrum={spectrum} index={index} /></> : null}
  </div>
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

function SpectrumFacts({ spectrum, index }: { spectrum: SpxtacularSpectrum, index: number }) {
  const metadata = spectrum.metadata
  const precursor = metadata.precursors?.[0]
  const rt = metadata.rt === null || metadata.rt === undefined ? 'Unknown' : `${(metadata.rt / 60).toFixed(2)} min`
  const facts = [
    ['Position', String(index)],
    ['Scan', String(metadata.scan_number ?? 'Unknown')],
    ['MS level', metadata.ms_level ? `MS${metadata.ms_level}` : 'Unknown'],
    ['Representation', metadata.spectrum_type ?? 'Unknown'],
    ['Peaks', spectrum.arrays.mz.length.toLocaleString()],
    ['Retention time', rt],
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
