import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ProcessingBatchPreview, ProcessingProfile } from '../types'
import { ProcessDialog } from './ProcessDialog'

vi.mock('../api/client', () => ({ api: { processingProfiles: vi.fn(), experiments: vi.fn(), previewProcessingBatch: vi.fn(), createProcessingBatch: vi.fn() } }))
afterEach(() => { cleanup()
  vi.resetAllMocks() })
const profile: ProcessingProfile = { id: 'standard', name: 'Standard mzML', enabled: true, system: true, revision: 1, outputFormat: 'mzML', createdAt: '', updatedAt: '', parameters: { filters: [], mzPrecision: 64, intensityPrecision: 32, compression: 'zlib', indexed: true } }
const preview: ProcessingBatchPreview = { scopeType: 'project', runCount: 1, targetCount: 1, queueCount: 1, currentCount: 0, staleCount: 0, incompatibleCount: 0, queuedCount: 0 }
function setup() {
  vi.mocked(api.processingProfiles).mockResolvedValue([profile])
  vi.mocked(api.experiments).mockResolvedValue([])
  vi.mocked(api.previewProcessingBatch).mockResolvedValue(preview)
  return render(<MemoryRouter><ProcessDialog projectId="project" onClose={vi.fn()} /></MemoryRouter>)
}

it('allows clearing every profile without selecting the default again', async () => {
  setup()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Queue 1 jobs' })).toBeEnabled())
  fireEvent.click(screen.getByRole('checkbox', { name: /Standard mzML/ }))
  expect(screen.getByRole('checkbox', { name: /Standard mzML/ })).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Queue 0 jobs' })).toBeDisabled()
  expect(screen.getByText('Select a scope and at least one profile.')).toBeVisible()
})

it('rejects stale previews and disables queueing after a preview failure', async () => {
  setup()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Queue 1 jobs' })).toBeEnabled())
  let complete: (value: ProcessingBatchPreview) => void = () => undefined
  vi.mocked(api.previewProcessingBatch).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  fireEvent.change(screen.getByLabelText('Existing output handling'), { target: { value: 'force' } })
  expect(screen.getByRole('button', { name: 'Queue 0 jobs' })).toBeDisabled()
  vi.mocked(api.previewProcessingBatch).mockRejectedValueOnce(new Error('Preview unavailable'))
  fireEvent.change(screen.getByLabelText('Existing output handling'), { target: { value: 'missing_or_stale' } })
  expect(await screen.findByRole('alert')).toHaveTextContent('Preview unavailable')
  await act(async () => complete(preview))
  expect(screen.getByRole('button', { name: 'Queue 0 jobs' })).toBeDisabled()
  expect(api.createProcessingBatch).not.toHaveBeenCalled()
})
