import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SpectrumExplorer, SpectrumPlot } from './SpectrumExplorer'
import type { Artifact, Job, SpectrumCatalogPage, SpxtacularSpectrum } from '../types'


const artifact: Artifact = {
  id: 'artifact-1',
  name: 'sample.mgf',
  format: 'MGF',
  role: 'source',
  sizeBytes: 120,
  checksum: 'sha256:test',
  status: 'verified'
}

const rawArtifact: Artifact = {
  ...artifact,
  id: 'raw-artifact',
  name: 'sample.raw',
  format: 'RAW'
}

const spectrum: SpxtacularSpectrum = {
  schema: 'spxtacular.spectrum',
  schema_version: 1,
  kind: 'msn_spectrum',
  arrays: {
    mz: [100.0, 150.5, 200.0],
    intensity: [10.0, 50.0, 25.0],
    charge: null,
    im: null,
    iso_score: null
  },
  metadata: {
    spectrum_type: 'centroid',
    denoised: null,
    normalized: null,
    deconvolution: null,
    scan_number: 42,
    ms_level: 2,
    native_id: 'scan=42',
    rt: 90,
    precursors: [{ mz: 500.25, intensity: 100, charge: 2, im: null, iso_score: null, is_monoisotopic: true }]
  }
}

const catalog: SpectrumCatalogPage = {
  schema: 'spectarr.spectrum-catalog',
  schema_version: 2,
  strategy: 'persistent',
  total: 2,
  limit: 12,
  next_cursor: null,
  items: [
    { id: 'entry-42', index: 0, native_id: 'scan=42', scan_number: 42, ms_level: 2, rt: 90, precursor_mz: 500.25, precursor_charge: 2, peak_count: 3, total_ion_current: 85 },
    { id: 'entry-43', index: 1, native_id: 'scan=43', scan_number: 43, ms_level: 2, rt: 96, precursor_mz: 622.31, precursor_charge: 2, peak_count: 4, total_ion_current: 120 }
  ]
}

const queuedCatalogJob: Job = {
  id: 'job-catalog',
  kind: 'extract_metadata',
  runName: 'sample',
  status: 'queued',
  progress: 0,
  detail: 'extract_metadata',
  createdAt: '2026-08-26T00:00:00Z'
}

afterEach(cleanup)

describe('SpectrumExplorer', () => {
  it('loads an MGF MS2 spectrum and renders its scientific metadata', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} />)

    await waitFor(() => expect(loader).toHaveBeenCalledWith('artifact-1', { msLevel: 2, index: 0 }))
    expect(await screen.findByText('scan=42')).toBeInTheDocument()
    expect(screen.getByText('500.2500 (2+)')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'centroid spectrum' })).toBeInTheDocument()
  })

  it('loads the next zero-based spectrum position', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const entryLoader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} loadCatalogSpectrum={entryLoader} />)
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))

    fireEvent.click(await screen.findByRole('button', { name: /1.50 min 42 MS2/ }))
    await waitFor(() => expect(entryLoader).toHaveBeenLastCalledWith('artifact-1', 'entry-42'))
    fireEvent.click(screen.getByRole('button', { name: 'Next spectrum' }))

    await waitFor(() => expect(entryLoader).toHaveBeenLastCalledWith('artifact-1', 'entry-43'))
  })

  it('browses lightweight summaries and selects by native ID', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const entryLoader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} loadCatalogSpectrum={entryLoader} />)
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))

    expect(await screen.findByText('622.3100')).toBeInTheDocument()
    fireEvent.click(screen.getByText('43'))

    await waitFor(() => expect(entryLoader).toHaveBeenLastCalledWith('artifact-1', 'entry-43'))
  })

  it('finds the spectrum nearest a retention time in minutes', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const queryLoader = vi.fn().mockResolvedValue(catalog)
    const entryLoader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={queryLoader} loadCatalogSpectrum={entryLoader} />)

    fireEvent.change(screen.getByRole('spinbutton', { name: 'RT (min) minimum' }), { target: { value: '1.6' } })

    await waitFor(() => expect(queryLoader).toHaveBeenLastCalledWith('artifact-1', expect.objectContaining({ msLevels: [2], retentionTimeMin: 96 })))
    fireEvent.click(screen.getByText('43'))
    await waitFor(() => expect(entryLoader).toHaveBeenLastCalledWith('artifact-1', 'entry-43'))
  })

  it('moves the viewer to the first row after filtering excludes the selection', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const entryLoader = vi.fn().mockResolvedValue(spectrum)
    const queryLoader = vi.fn().mockImplementation((_artifactId: string, request: { scanNumberMin?: number }) => Promise.resolve(
      request.scanNumberMin === 43 ? { ...catalog, total: 1, items: [catalog.items[1]] } : catalog
    ))
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={queryLoader} loadCatalogSpectrum={entryLoader} />)

    await waitFor(() => expect(entryLoader).toHaveBeenLastCalledWith('artifact-1', 'entry-42'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Scan minimum' }), { target: { value: '43' } })

    await waitFor(() => expect(entryLoader).toHaveBeenLastCalledWith('artifact-1', 'entry-43'))
  })

  it('uses the preferred run MS level for a RAW artifact', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[rawArtifact]} preferredMsLevel={2} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} />)

    await waitFor(() => expect(loader).toHaveBeenCalledWith('raw-artifact', { msLevel: 2, index: 0 }))
  })

  it('prefers an mzML derivative over a RAW source for reliable catalog access', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const mzmlArtifact = { ...artifact, id: 'mzml-artifact', name: 'sample.mzML', format: 'mzML' as const, role: 'derived' as const }
    render(<SpectrumExplorer artifacts={[rawArtifact, mzmlArtifact]} preferredMsLevel={2} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} />)

    expect(screen.getByRole('combobox', { name: 'Spectrum artifact' })).toHaveValue('mzml-artifact')
    await waitFor(() => expect(loader).toHaveBeenCalledWith('mzml-artifact', { msLevel: 2, index: 0 }))
  })

  it('polls a catalog build and reports the real extraction failure', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const fallbackCatalog = { ...catalog, schema_version: 1 as const, strategy: 'fallback' as const }
    const buildCatalog = vi.fn().mockResolvedValue(queuedCatalogJob)
    const loadJob = vi.fn().mockResolvedValue({ ...queuedCatalogJob, status: 'failed', detail: 'Reader could not open this RAW file' })
    render(<SpectrumExplorer artifacts={[rawArtifact]} preferredMsLevel={2} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(fallbackCatalog)} buildCatalog={buildCatalog} loadJob={loadJob} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Build catalog' }))

    await waitFor(() => expect(loadJob).toHaveBeenCalledWith('job-catalog'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Reader could not open this RAW file')
    expect(screen.getByRole('button', { name: 'Retry catalog' })).toBeEnabled()
  })

  it('loads the indexed catalog when a catalog build completes', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    const fallbackCatalog = { ...catalog, schema_version: 1 as const, strategy: 'fallback' as const }
    const queryLoader = vi.fn().mockResolvedValueOnce(fallbackCatalog).mockResolvedValue(catalog)
    const buildCatalog = vi.fn().mockResolvedValue(queuedCatalogJob)
    const loadJob = vi.fn().mockResolvedValue({ ...queuedCatalogJob, status: 'complete', progress: 100 })
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={queryLoader} buildCatalog={buildCatalog} loadJob={loadJob} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Build catalog' }))

    expect(await screen.findByText('Indexed catalog')).toBeInTheDocument()
    expect(queryLoader).toHaveBeenCalledTimes(2)
  })

  it('disables unavailable levels and stops at a known spectrum boundary', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[rawArtifact]} preferredMsLevel={2} spectrumCounts={{ '1': 0, '2': 1 }} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue({ ...catalog, total: 1, items: [catalog.items[0]] })} />)

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('checkbox', { name: 'MS1' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next spectrum' })).toBeDisabled()
  })

  it('preserves a narrow profile apex while reducing browser plot points', () => {
    const mz = Array.from({ length: 4001 }, (_, index) => index)
    const intensity = mz.map(() => 1)
    intensity[1999] = 1000
    const profile = {
      ...spectrum,
      arrays: { ...spectrum.arrays, mz, intensity },
      metadata: { ...spectrum.metadata, spectrum_type: 'profile' }
    }

    render(<SpectrumPlot spectrum={profile} />)

    const trace = screen.getByRole('img', { name: 'profile spectrum' }).querySelector('polyline')
    expect(trace?.getAttribute('points')).toContain(',18.00')
    expect(trace?.getAttribute('points')?.split(' ').length).toBeLessThanOrEqual(2000)
  })

  it('formats a negative precursor charge with a minus sign', async () => {
    const loader = vi.fn().mockResolvedValue({
      ...spectrum,
      metadata: { ...spectrum.metadata, precursors: [{ ...spectrum.metadata.precursors![0], charge: -2 }] }
    })
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} />)

    expect(await screen.findByText('500.2500 (2-)')).toBeInTheDocument()
  })

  it('combines a vendor charge magnitude with negative scan polarity', async () => {
    const loader = vi.fn().mockResolvedValue({
      ...spectrum,
      metadata: {
        ...spectrum.metadata,
        polarity: 'negative',
        precursors: [{ ...spectrum.metadata.precursors![0], charge: 2 }]
      }
    })
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} loadQuery={vi.fn().mockResolvedValue(catalog)} />)

    expect(await screen.findByText('500.2500 (2-)')).toBeInTheDocument()
  })

  it('shows additional scan and per-peak metadata', async () => {
    const detailed = {
      ...spectrum,
      arrays: {
        ...spectrum.arrays,
        charge: [1, -2, 3],
        im: [0.9, 1.1, 1.3],
        iso_score: [0.7, 0.8, 0.9]
      },
      metadata: {
        ...spectrum.metadata,
        analyzer: 'orbitrap',
        activation_type: 'HCD',
        collision_energy: 28
      }
    }
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={vi.fn().mockResolvedValue(detailed)} loadQuery={vi.fn().mockResolvedValue(catalog)} />)
    const chart = await screen.findByRole('img', { name: 'centroid spectrum' })
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 820, height: 270, top: 0, right: 820, bottom: 270, left: 0, toJSON: () => ({}) })

    fireEvent.mouseMove(chart, { clientX: 410 })

    expect(screen.getByText('orbitrap')).toBeInTheDocument()
    expect(screen.getByText('HCD')).toBeInTheDocument()
    expect(screen.getByText('2- charge')).toBeInTheDocument()
    expect(screen.getByText(/1.10000/)).toBeInTheDocument()
    expect(screen.getByText(/0.8000 isotope score/)).toBeInTheDocument()
  })

  it('explains when no supported artifact exists', () => {
    const unsupported = { ...artifact, format: 'Parquet' as const }
    render(<SpectrumExplorer artifacts={[unsupported]} loadSpectrum={vi.fn()} />)
    expect(screen.getByText(/No RAW, mzML, MGF, MS2, or MSP artifact/)).toBeInTheDocument()
  })
})
