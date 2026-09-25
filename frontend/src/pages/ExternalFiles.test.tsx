import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ExternalInventory } from './ExternalFiles'
import { externalApi, type ExternalEntry } from '../api/external'
import { api } from '../api/client'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }))
vi.mock('../api/client', () => ({ api: { experiments: vi.fn(), agents: vi.fn() } }))
vi.mock('../api/external', () => ({ externalApi: { roots: vi.fn(), search: vi.fn(), tasks: vi.fn(), queue: vi.fn(), cancel: vi.fn(), update: vi.fn(), register: vi.fn(), history: vi.fn(), matches: vi.fn(), relocate: vi.fn() } }))
const entry: ExternalEntry = {
  id: 'entry', name: 'sample.mgf', kind: 'file', format: 'MGF', locations: [{
    id: 'loc', root_id: 'root', root_label: 'Research archive', root_path: '/archive', relative_path: 'sample.mgf', status: 'observed', root_status: 'available', enabled: true,
    readiness: 'stable_by_observation', byte_size: 20, revision_id: 'rev', observed_at: '2026-09-25T00:00:00Z', verified_at: '2026-09-25T00:00:00Z', freshness: 'recent', root_freshness: 'recent', agent_freshness: 'recent', host: 'instrument-pc'
  }]
}
beforeEach(() => {
  vi.mocked(api.experiments).mockResolvedValue([{ id: 'experiment', name: 'Study' }] as never)
  vi.mocked(api.agents).mockResolvedValue([])
  vi.mocked(externalApi.roots).mockResolvedValue({ items: [{ id: 'root', agent_id: 'agent', label: 'Research archive', path: '/archive', enabled: true, status: 'available', freshness: 'recent' }] })
  vi.mocked(externalApi.search).mockResolvedValue({ items: [entry], total: 1, next_cursor: null })
  vi.mocked(externalApi.tasks).mockResolvedValue({ items: [] })
  vi.mocked(externalApi.queue).mockResolvedValue({})
})
afterEach(() => { cleanup()
  vi.resetAllMocks()
})
const show = () => render(<MemoryRouter><ExternalInventory projectId="project" /></MemoryRouter>)

it('does not scan on read and pins a selected import revision', async () => {
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'sample.mgf' }))
  expect(externalApi.queue).not.toHaveBeenCalled()
  expect(screen.getByText(/This path belongs to the named machine/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Import verified revision' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Import into experiment'), { target: { value: 'experiment' } })
  fireEvent.click(screen.getByRole('button', { name: 'Import verified revision' }))
  await waitFor(() => expect(externalApi.queue).toHaveBeenCalledWith('root', 'import', { location_id: 'loc', revision_id: 'rev', experiment_id: 'experiment' }))
})

it('shows unavailable roots without claiming their files were deleted', async () => {
  vi.mocked(externalApi.search).mockResolvedValue({ items: [{ ...entry, locations: [{ ...entry.locations[0], root_status: 'unavailable' }] }], total: 1, next_cursor: null })
  show()
  expect(await screen.findByText('Location unavailable')).toBeInTheDocument()
  expect(screen.queryByText('Not found at this location')).not.toBeInTheDocument()
})

it('requires an explicit scan and disables repeat requests while pending', async () => {
  vi.mocked(externalApi.tasks).mockResolvedValue({ items: [{ id: 'task', kind: 'scan', state: 'pending' }] })
  show()
  expect(await screen.findByRole('button', { name: 'Scan Research archive' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel request' }))
  await waitFor(() => expect(externalApi.cancel).toHaveBeenCalledWith('task'))
})
