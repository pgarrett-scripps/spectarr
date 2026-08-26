import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { Processing } from './Processing'

vi.mock('../api/client', () => ({
  api: {
    jobs: vi.fn(),
    processingBatches: vi.fn(),
    processingBatch: vi.fn(),
    retryProcessingBatch: vi.fn(),
    cancelProcessingBatch: vi.fn()
  }
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Processing', () => {
  it('shows spectrum catalog extraction jobs alongside processing batches', async () => {
    vi.mocked(api.processingBatches).mockResolvedValue([])
    vi.mocked(api.jobs).mockResolvedValue([{
      id: 'catalog-job',
      kind: 'extract_metadata',
      runName: 'sample-run',
      status: 'failed',
      progress: 0,
      detail: 'Reader could not open the selected RAW file',
      createdAt: '2026-08-26T00:00:00Z'
    }])

    render(<Processing />)

    expect(await screen.findByRole('heading', { name: 'Spectrum catalogs' })).toBeInTheDocument()
    expect(screen.getByText('sample-run')).toBeInTheDocument()
    expect(screen.getByText('Reader could not open the selected RAW file')).toBeInTheDocument()
    expect(screen.queryByText('No processing jobs')).not.toBeInTheDocument()
  })
})
