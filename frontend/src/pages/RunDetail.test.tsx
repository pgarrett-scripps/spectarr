import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation, Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Run } from '../types'
import { RunDetail } from './RunDetail'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }))

vi.mock('../api/client', () => ({
  api: {
    run: vi.fn(), generateArtifact: vi.fn(), extractArtifact: vi.fn(), job: vi.fn()
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
  it('shows unknown acquisition time and explains converted fallback observations', async () => {
    vi.mocked(api.run).mockResolvedValue({ ...run, acquiredAt: undefined, spectraCount: 0, durationMinutes: 0, extraction: {
      id: 'e', status: 'succeeded', extractor: 'test', extractorVersion: '1', schemaVersion: '1', sourceSha256: '',
      warnings: [], spectraByMsLevel: {}, polarities: [], chargeCounts: {}, tic: [], bpc: [], raw: {},
      artifactId: 'converted', artifactName: 'converted.mzML', selectionReason: 'linked_open_format_fallback'
    } })
    render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1']}>
      <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
    </MemoryRouter>)
    await screen.findByRole('heading', { name: 'Alpha run' })
    expect(screen.getByText('Acquired').nextElementSibling).toHaveTextContent('Unknown')
    expect(screen.getByText('Total spectra').nextElementSibling).toHaveTextContent('0')
    expect(screen.getByText('Duration').nextElementSibling).toHaveTextContent('0 sec')
    expect(screen.getByText('Observed in converted.mzML')).toBeVisible()
    expect(screen.getByText(/These values describe a linked converted file/)).toBeVisible()
  })

  it('does not offer downloads for directory bundles', async () => {
    vi.mocked(api.run).mockResolvedValue({ ...run, artifacts: [{ ...run.artifacts[0], isDirectory: true }] })
    render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/files']}>
      <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
    </MemoryRouter>)
    expect(await screen.findByText('stored')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download source' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download alpha.raw' })).toBeDisabled()
    expect(screen.getByText('Locate file')).toBeInTheDocument()
  })

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

it('navigating directly between project runs reaches the requested run', async () => {
  vi.mocked(api.run).mockImplementation(async id => id === 'run-2'
    ? { ...run, id: 'run-2', projectId: 'project-2', name: 'Beta run' }
    : run)
  render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1']}>
    <Link to="/projects/project-2/runs/run-2">Next run</Link>
    <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
  </MemoryRouter>)
  await screen.findByRole('heading', { name: 'Alpha run' })
  fireEvent.click(screen.getByText('Next run'))
  expect(await screen.findByRole('heading', { name: 'Beta run' })).toBeVisible()
})

it('generating an output refreshes run data without reloading the browser', async () => {
  vi.mocked(api.run).mockResolvedValue(run)
  vi.mocked(api.generateArtifact).mockResolvedValue({ id: 'job-1', status: 'queued' } as never)
  render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/processing']}>
    <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
  </MemoryRouter>)
  await screen.findByRole('heading', { name: 'Alpha run' })
  fireEvent.click(screen.getByRole('button', { name: /Generate mzML/ }))
  await screen.findByText('mzML job job-1 is queued')
  await waitFor(() => expect(api.run).toHaveBeenCalledTimes(2))
})

it.each(['complete', 'failed', 'cancelled'] as const)('refreshes completed job data when processing becomes %s', async status => {
  vi.mocked(api.run).mockResolvedValue(run)
  vi.mocked(api.extractArtifact).mockResolvedValue({ id: 'job-1', status: 'queued' } as never)
  vi.mocked(api.job).mockResolvedValue({ id: 'job-1', status, detail: 'Provider result' } as never)
  render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/processing']}>
    <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
  </MemoryRouter>)
  await screen.findByRole('heading', { name: 'Alpha run' })
  vi.useFakeTimers()
  try {
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Extract metadata' })))
    vi.mocked(api.run).mockResolvedValue({ ...run, name: 'Updated run' })
    await act(async () => vi.advanceTimersByTimeAsync(1500))
    expect(api.job).toHaveBeenCalledWith('job-1')
    expect(screen.getByRole('heading', { name: 'Updated run' })).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent(`Job job-1 is ${status}`)
    const calls = vi.mocked(api.job).mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(6000))
    expect(api.job).toHaveBeenCalledTimes(calls)
  } finally {
    vi.useRealTimers()
  }
})


it('shows current job progress, failures, and stored output links', async () => {
  vi.mocked(api.run).mockResolvedValue({ ...run, status: 'processing',
    artifacts: [...run.artifacts, { ...run.artifacts[0], id: 'output', role: 'derived', name: 'alpha.mzML', format: 'mzML' }],
    processingJobs: [
      { id: 'convert', kind: 'convert', inputArtifactId: 'artifact-1', outputFormat: 'mzML', status: 'running', progress: 42, detail: 'Reading spectra', createdAt: '', runName: run.name },
      { id: 'extract', kind: 'extract_metadata', inputArtifactId: 'artifact-1', status: 'failed', progress: 0, detail: 'Reader could not open source', createdAt: '', runName: run.name }
    ]
  })
  render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/processing']}>
    <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByRole('button', { name: /mzML running/ })).toBeDisabled()
  expect(screen.getByText('Reader could not open source')).toBeVisible()
  expect(screen.getByRole('link', { name: 'View file' })).toHaveAttribute('href', '/projects/project-1/runs/run-1/files')
  expect(screen.getByRole('link', { name: 'Review and retry in Processing' })).toBeVisible()
})

it('omits the source format and disables actions for unavailable sources', async () => {
  vi.mocked(api.run).mockResolvedValue({ ...run, artifacts: [{ ...run.artifacts[0], format: 'MGF', status: 'purged' }] })
  render(<MemoryRouter initialEntries={['/projects/project-1/runs/run-1/processing']}>
    <Routes><Route path="/projects/:projectId/runs/:runId/:tab?" element={<RunDetail />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByRole('button', { name: /Generate mzML/ })).toBeDisabled()
  expect(screen.queryByRole('button', { name: /Generate MGF/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Extract metadata' })).toBeDisabled()
})
