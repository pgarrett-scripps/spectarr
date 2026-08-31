import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { api } from '../api/client'
import type { Run } from '../types'
import { Runs } from './Runs'

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  api: {
    experiments: vi.fn(),
    project: vi.fn(),
    runs: vi.fn()
  }
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', username: 'tester', displayName: 'Tester', role: 'viewer', active: true }
  })
}))

const baseRun: Run = {
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
  metadata: {},
  artifacts: [],
  assignmentStatus: 'assigned'
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('project run list', () => {
  it('treats experiments as filters inside the project', async () => {
    vi.mocked(api.project).mockResolvedValue({
      id: 'project-1',
      name: 'Proteomics study',
      description: 'Study',
      runCount: 2,
      sizeBytes: 200,
      updatedAt: '2026-08-21T00:00:00Z',
      metadata: {}
    })
    vi.mocked(api.experiments).mockResolvedValue([
      { id: 'experiment-1', projectId: 'project-1', name: 'Control cohort' },
      { id: 'experiment-2', projectId: 'project-1', name: 'Treatment cohort' }
    ])
    vi.mocked(api.runs).mockResolvedValue([
      baseRun,
      { ...baseRun, id: 'run-2', experimentId: 'experiment-2', name: 'Beta run', experimentName: 'Treatment cohort' }
    ])

    render(<MemoryRouter initialEntries={['/projects/project-1/runs']}>
      <Routes><Route path="/projects/:projectId/runs" element={<Runs />} /></Routes>
    </MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Proteomics study' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Runs' })).toHaveClass('active')
    expect(screen.getByText('Alpha run')).toBeInTheDocument()
    expect(screen.getByText('Beta run')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Import run' })).toHaveAttribute('href', '/runs/import?project=project-1')

    fireEvent.click(screen.getByRole('button', { name: /Control cohort/ }))

    const table = screen.getByRole('table')
    expect(within(table).getByText('Alpha run')).toBeInTheDocument()
    expect(within(table).queryByText('Beta run')).not.toBeInTheDocument()
  })

  it('clears stale experiment links instead of showing a false empty project', async () => {
    vi.mocked(api.project).mockResolvedValue({
      id: 'project-1',
      name: 'Proteomics study',
      runCount: 1,
      sizeBytes: 100,
      updatedAt: '2026-08-21T00:00:00Z',
      metadata: {}
    })
    vi.mocked(api.experiments).mockResolvedValue([{ id: 'experiment-1', projectId: 'project-1', name: 'Control cohort' }])
    vi.mocked(api.runs).mockResolvedValue([baseRun])

    render(<MemoryRouter initialEntries={['/projects/project-1/runs?experiment=deleted-experiment']}>
      <Routes><Route path="/projects/:projectId/runs" element={<Runs />} /></Routes>
    </MemoryRouter>)

    expect(await screen.findByText('Alpha run')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /All/ })).toHaveClass('active'))
    expect(screen.queryByText('No runs found')).not.toBeInTheDocument()
  })

  it('offers to clear filters when a project search has no matches', async () => {
    vi.mocked(api.project).mockResolvedValue({
      id: 'project-1',
      name: 'Proteomics study',
      runCount: 1,
      sizeBytes: 100,
      updatedAt: '2026-08-21T00:00:00Z',
      metadata: {}
    })
    vi.mocked(api.experiments).mockResolvedValue([{ id: 'experiment-1', projectId: 'project-1', name: 'Control cohort' }])
    vi.mocked(api.runs).mockResolvedValue([baseRun])

    render(<MemoryRouter initialEntries={['/projects/project-1/runs?q=missing']}>
      <Routes><Route path="/projects/:projectId/runs" element={<Runs />} /></Routes>
    </MemoryRouter>)

    fireEvent.click(await screen.findByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByText('Alpha run')).toBeInTheDocument()
  })
})
