import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SpectrumExplorer, SpectrumPlot } from './SpectrumExplorer'
import type { Artifact, SpxtacularSpectrum } from '../types'


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

afterEach(cleanup)

describe('SpectrumExplorer', () => {
  it('loads an MGF MS2 spectrum and renders its scientific metadata', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} />)

    await waitFor(() => expect(loader).toHaveBeenCalledWith('artifact-1', { msLevel: 2, index: 0 }))
    expect(await screen.findByText('scan=42')).toBeInTheDocument()
    expect(screen.getByText('500.2500 (2+)')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'centroid spectrum' })).toBeInTheDocument()
  })

  it('loads the next zero-based spectrum position', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} />)
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole('button', { name: 'Next spectrum' }))

    await waitFor(() => expect(loader).toHaveBeenLastCalledWith('artifact-1', { msLevel: 2, index: 1 }))
  })

  it('uses the preferred run MS level for a RAW artifact', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[rawArtifact]} preferredMsLevel={2} loadSpectrum={loader} />)

    await waitFor(() => expect(loader).toHaveBeenCalledWith('raw-artifact', { msLevel: 2, index: 0 }))
  })

  it('disables unavailable levels and stops at a known spectrum boundary', async () => {
    const loader = vi.fn().mockResolvedValue(spectrum)
    render(<SpectrumExplorer artifacts={[rawArtifact]} preferredMsLevel={2} spectrumCounts={{ '1': 0, '2': 1 }} loadSpectrum={loader} />)

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'MS1' })).toBeDisabled()
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
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} />)

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
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={loader} />)

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
    render(<SpectrumExplorer artifacts={[artifact]} loadSpectrum={vi.fn().mockResolvedValue(detailed)} />)
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
