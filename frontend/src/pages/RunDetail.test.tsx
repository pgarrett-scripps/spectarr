import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { api } from '../api/client'
import type { Run } from '../types'
import { RunDetail } from './RunDetail'

vi.mock('../api/client', () => ({
  api: {
    run: vi.fn()
  },
  downloadArtifact: vi.fn()
}))

vi.mock('../components/SpectrumExplorer', () => ({
  SpectrumExplorer: () => <div>Interactive spectrum content</div>
}))

const run: Run = {
  id: 'run-1',
  projectId: 'project-1',
  experimentId: 'experiment-1',
  name: 'Alpha run',
  projectName: 'Proteomics study',
  experimentName: 'Control cohort',
  sampleName: 'Sample A',
  instrument: 'Orbitrap',
  acquiredAt: '2026-08-20T00:00:00Z',
  importedAt: '2026-08-21T00:00:00Z',
  status: 'ready',
  sourceFormat: 'RAW',
  sizeBytes: 100,
  spectraCount: 10,
  ms2Count: 8,
  metadata: {},
  artifacts: [{
    id: 'artifact-1',
    name: 'alpha.raw',
    format: 'RAW',
    role: 'source',
    sizeBytes: 100,
    checksum: 'abc123',
    status: 'verified'
  }],
  assignmentStatus: 'assigned'
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('run detail sections', () => {
  it('opens the spectrum viewer as a dedicated run section', async () => {
    vi.mocked(api.run).mockResolvedValue(run)

    render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/spectra']}>
      <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
    </MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Alpha run' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Spectra' })).toHaveClass('active')
    expect(screen.getByRole('heading', { name: 'Spectrum viewer' })).toBeInTheDocument()
    expect(screen.getByText('Interactive spectrum content')).toBeInTheDocument()
    expect(screen.queryByText('Scientific metadata')).not.toBeInTheDocument()
  })

  it('repairs a mismatched project route using the run canonical path', async () => {
    vi.mocked(api.run).mockResolvedValue(run)

    function Location() {
      return <div data-testid="location">{useLocation().pathname}</div>
    }

    render(<MemoryRouter initialEntries={['/projects/wrong-project/runs/run-1/spectra']}>
      <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<><RunDetail /><Location /></>} /></Routes>
    </MemoryRouter>)

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/project-1/runs/run-1/spectra'))
    expect(screen.getByRole('link', { name: 'Spectra' })).toHaveClass('active')
  })

  it('redirects unknown sections to the run summary', async () => {
    vi.mocked(api.run).mockResolvedValue(run)

    function Location() {
      return <div data-testid="location">{useLocation().pathname}</div>
    }

    render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/unknown']}>
      <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<><RunDetail /><Location /></>} /></Routes>
    </MemoryRouter>)

    expect(await screen.findByTestId('location')).toHaveTextContent('/projects/project-1/runs/run-1')
    expect(await screen.findByText('Scientific metadata')).toBeInTheDocument()
  })
})
