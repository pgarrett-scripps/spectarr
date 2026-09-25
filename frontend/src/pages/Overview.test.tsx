import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { OverviewData } from '../types'
import { Overview } from './Overview'

vi.mock('../api/client', async importOriginal => ({ ...await importOriginal<typeof import('../api/client')>(), api: { overview: vi.fn() } }))
const auth = vi.hoisted(() => ({ user: { role: 'admin' } }))
vi.mock('../auth/AuthContext', () => ({ useAuth: () => auth }))
const data: OverviewData = {
  runs: [], projects: [], storage: [],
  jobs: [{ id: 'job', kind: 'extract_metadata', runName: 'Sample 01', status: 'queued', progress: 0, detail: 'extract_metadata', createdAt: '2026-09-24T12:00:00Z' }],
  health: { api: 'online', workers: 0, queueDepth: 23, version: '0.3.0' },
  stats: { runs: 10, artifacts: 14, formatCounts: {} }
}
beforeEach(() => {
  auth.user.role = 'admin'
  vi.mocked(api.overview).mockResolvedValue(data)
})
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
function setup() {
  render(<MemoryRouter><Overview /></MemoryRouter>)
}

it('shows a loading state before counts and uses the whole queue instead of the recent jobs sample', async () => {
  setup()
  expect(screen.getByRole('status')).toHaveTextContent('Loading library overview')
  await screen.findByText('23 jobs queued or running')
  expect(screen.getByText('Processing', { selector: 'dt' }).nextElementSibling).toHaveTextContent('23')
  expect(screen.getByText('Files', { selector: 'dt' }).nextElementSibling).toHaveTextContent('14')
  expect(screen.getByText('Logical file size')).toBeVisible()
  expect(screen.getByRole('link', { name: 'Import data' })).toHaveAttribute('href', '/runs/import')
})

it('offers shared-library guidance and hides import for viewers', async () => {
  auth.user.role = 'viewer'
  setup()
  await screen.findByText('Projects and acquisitions shared with you will appear here.')
  expect(screen.queryByRole('link', { name: 'Import data' })).not.toBeInTheDocument()
})

it('does not present a failed request as an empty library and supports retry', async () => {
  vi.mocked(api.overview).mockRejectedValueOnce(new Error('Server unavailable'))
  setup()
  expect(await screen.findByRole('alert')).toHaveTextContent('Server unavailable')
  expect(screen.queryByText('Acquisitions', { selector: 'dt' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText('23 jobs queued or running')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
