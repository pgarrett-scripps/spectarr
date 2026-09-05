import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
    runs: vi.fn(),
    runPage: vi.fn()
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

function mockRunPages(runs: Run[]) {
  vi.mocked(api.runPage).mockImplementation(async (values = {}) => {
    const items = runs.filter(run => (!values.experimentId || run.experimentId === values.experimentId)
      && (!values.query || run.name.toLowerCase().includes(values.query.toLowerCase())))
    const offset = values.offset ?? 0
    const limit = values.limit ?? 50
    return {
      items: items.slice(offset, offset + limit), total: items.length,
      nextOffset: offset + limit < items.length ? offset + limit : null,
      experimentCounts: Object.fromEntries(runs.map(run => [run.experimentId, runs.filter(item => item.experimentId === run.experimentId).length]))
    }
  })
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
    mockRunPages([
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
    expect(screen.queryByRole('link', { name: 'Import run' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Control cohort/ }))

    expect(await screen.findByText('Alpha run')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Beta run')).not.toBeInTheDocument())
    expect(api.runPage).toHaveBeenLastCalledWith(expect.objectContaining({ experimentId: 'experiment-1', projectId: 'project-1' }))
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
    mockRunPages([baseRun])

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
    mockRunPages([baseRun])

    render(<MemoryRouter initialEntries={['/projects/project-1/runs?q=missing']}>
      <Routes><Route path="/projects/:projectId/runs" element={<Runs />} /></Routes>
    </MemoryRouter>)

    fireEvent.click(await screen.findByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByText('Alpha run')).toBeInTheDocument()
  })
})

it('pages through runs and resets pagination when filters change', async () => {
  vi.mocked(api.project).mockResolvedValue({ id: 'project-1', name: 'Study', runCount: 102, sizeBytes: 0, updatedAt: '', metadata: {} })
  vi.mocked(api.experiments).mockResolvedValue([{ id: 'experiment-1', projectId: 'project-1', name: 'Control cohort' }])
  mockRunPages(Array.from({ length: 102 }, (_, index) => ({ ...baseRun, id: `run-${index}`, name: `Acquisition ${index}` })))
  render(<MemoryRouter initialEntries={['/projects/project-1/runs']}>
    <Routes><Route path="/projects/:projectId/runs" element={<Runs />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByText('Acquisition 0')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText('Acquisition 50')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText('Acquisition 101')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: 'Filter runs' }), { target: { value: 'Acquisition 0' } })
  expect(await screen.findByText('Acquisition 0')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
})

it('keeps the search field mounted while the next result is loading', async () => {
  vi.mocked(api.project).mockResolvedValue({ id: 'project-1', name: 'Study', runCount: 1, sizeBytes: 0, updatedAt: '', metadata: {} })
  vi.mocked(api.experiments).mockResolvedValue([])
  mockRunPages([baseRun])
  render(<MemoryRouter initialEntries={['/projects/project-1/runs']}>
    <Routes><Route path="/projects/:projectId/runs" element={<Runs />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByText('Alpha run')).toBeInTheDocument()
  vi.mocked(api.runPage).mockImplementation(() => new Promise(() => {}))
  const field = screen.getByRole('textbox', { name: 'Filter runs' })
  field.focus()
  fireEvent.change(field, { target: { value: 'Al' } })
  await waitFor(() => expect(screen.getAllByText('Loading runs').length).toBeGreaterThan(0))
  expect(screen.getByRole('textbox', { name: 'Filter runs' })).toBe(field)
  expect(field).toHaveFocus()
})
