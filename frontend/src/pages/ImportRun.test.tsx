import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { api } from '../api/client'
import { ImportRun } from './ImportRun'

vi.mock('../api/client', () => ({
  api: {
    experiments: vi.fn(),
    importRun: vi.fn(),
    project: vi.fn()
  }
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('project-context import', () => {
  it('locks the destination project and suggests its existing experiments', async () => {
    vi.mocked(api.project).mockResolvedValue({
      id: 'project-1',
      name: 'Proteomics study',
      runCount: 1,
      sizeBytes: 100,
      updatedAt: '2026-08-21T00:00:00Z',
      metadata: {}
    })
    vi.mocked(api.experiments).mockResolvedValue([
      { id: 'experiment-1', projectId: 'project-1', name: 'Control cohort' }
    ])

    render(<MemoryRouter initialEntries={['/runs/import?project=project-1']}>
      <Routes><Route path="/runs/import" element={<ImportRun />} /></Routes>
    </MemoryRouter>)

    expect(await screen.findByDisplayValue('Proteomics study')).toHaveAttribute('readonly')
    expect(await screen.findByDisplayValue('Control cohort')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Cancel' })).toHaveAttribute('href', '/projects/project-1/runs')
  })
})
