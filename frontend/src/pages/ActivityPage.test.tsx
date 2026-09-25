import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { ActivityPage } from './ActivityPage'

const role = vi.hoisted(() => ({ value: 'admin' }))
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { role: role.value } }) }))
vi.mock('../api/client', () => ({ api: { jobs: vi.fn(), retryJob: vi.fn(), cancelQueuedJob: vi.fn() } }))
const job = { id: 'queued-job', kind: 'convert' as const, runName: 'sample', status: 'queued' as const, progress: 0, detail: 'Conversion', createdAt: '2026-09-09T00:00:00Z' }
afterEach(() => { cleanup()
  vi.resetAllMocks()
  role.value = 'admin' })

it('cancels a queued job, displays cancellation, and allows retry', async () => {
  vi.mocked(api.jobs).mockResolvedValueOnce([job]).mockResolvedValue([{ ...job, status: 'cancelled' }])
  render(<ActivityPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel queued' }))
  await waitFor(() => expect(api.cancelQueuedJob).toHaveBeenCalledWith(job.id))
  expect(await screen.findByText('Cancelled')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'History' }))
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(api.retryJob).toHaveBeenCalledWith(job.id))
})

it('does not offer job mutations to viewers', async () => {
  role.value = 'viewer'
  vi.mocked(api.jobs).mockResolvedValue([job, { ...job, id: 'failed-job', status: 'failed' }])
  render(<ActivityPage />)
  await screen.findByText('Queued', { selector: '.status-badge' })
  expect(screen.queryByRole('button', { name: 'Cancel queued' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
})
