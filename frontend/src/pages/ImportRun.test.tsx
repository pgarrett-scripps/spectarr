import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { api } from '../api/client'
import { ImportRun } from './ImportRun'

vi.mock('../api/client', () => ({
  api: {
    experiments: vi.fn(),
    importRun: vi.fn(),
    prepareImport: vi.fn(),
    project: vi.fn()
  }
}))

beforeEach(() => {
  vi.mocked(api.prepareImport).mockResolvedValue({ projectId: 'project-1', experimentId: 'experiment-1' })
  vi.mocked(api.importRun).mockImplementation(async values => ({ id: values.runName, projectId: 'project-1' }))
})

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

function renderImport() {
  render(<MemoryRouter initialEntries={['/runs/import']}>
    <Routes><Route path="/runs/import" element={<ImportRun />} /></Routes>
  </MemoryRouter>)
  fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'Proteomics' } })
  fireEvent.change(screen.getByLabelText('Experiment'), { target: { value: 'Cohort' } })
}

function addFiles(...names: string[]) {
  fireEvent.change(screen.getByLabelText('Source files'), {
    target: { files: names.map(name => new File(['data'], name, { lastModified: 1 })) }
  })
}

describe('batch import', () => {
  it('previews names, avoids duplicate selections, and imports every queued file into one destination', async () => {
    renderImport()
    addFiles('sample-one.mzML.gz', 'sample-two.mgf')
    addFiles('sample-one.mzML.gz')
    expect(screen.getByLabelText('Run name 1')).toHaveValue('sample-one')
    expect(screen.getByLabelText('Sample 2')).toHaveValue('sample-two')
    expect(screen.queryByLabelText('Run name 3')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Run name pattern/), { target: { value: 'cohort-{index}-{name}' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply run names' }))
    fireEvent.change(screen.getByLabelText(/Sample for all runs/), { target: { value: 'Pooled QC' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply sample' }))
    expect(screen.getByLabelText('Run name 2')).toHaveValue('cohort-2-sample-two')
    fireEvent.click(screen.getByRole('button', { name: 'Import 2 runs' }))
    expect(await screen.findByText('2 of 2 imported')).toBeInTheDocument()
    expect(api.prepareImport).toHaveBeenCalledTimes(1)
    expect(api.importRun).toHaveBeenCalledTimes(2)
    expect(api.importRun).toHaveBeenNthCalledWith(2, expect.objectContaining({
      runName: 'cohort-2-sample-two', sampleName: 'Pooled QC',
      destination: { projectId: 'project-1', experimentId: 'experiment-1' }
    }))
    expect(screen.getByRole('link', { name: 'View project runs' })).toHaveAttribute('href', '/projects/project-1/runs')
  })

  it('continues after a failure, shows transfer progress, and retries only the failed item with its original key', async () => {
    let rejectFirst: (reason: Error) => void = () => {}
    vi.mocked(api.importRun).mockImplementationOnce(values => {
      values.onProgress?.({ phase: 'Uploading', percent: 37 })
      return new Promise((_resolve, reject) => { rejectFirst = reject })
    })
    renderImport()
    addFiles('one.mgf', 'two.mgf')
    fireEvent.click(screen.getByRole('button', { name: 'Import 2 runs' }))
    expect(await screen.findByText('Uploading 37%')).toBeInTheDocument()
    expect(api.importRun).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Run name 1')).toHaveAttribute('readonly')
    await act(async () => rejectFirst(new Error('Connection lost')))
    expect(await screen.findByText('1 of 2 imported, 1 failed')).toBeInTheDocument()
    expect(screen.getByText('Failed: Connection lost')).toBeInTheDocument()
    const originalKey = vi.mocked(api.importRun).mock.calls[0][0].idempotencyKey
    fireEvent.click(screen.getByRole('button', { name: 'Retry one' }))
    expect(await screen.findByText('2 of 2 imported')).toBeInTheDocument()
    expect(api.importRun).toHaveBeenCalledTimes(3)
    expect(api.importRun).toHaveBeenLastCalledWith(expect.objectContaining({ runName: 'one', idempotencyKey: originalKey }))
    expect(api.prepareImport).toHaveBeenCalledTimes(1)
  })

  it('queues multiple server paths and removes unwanted items before import', async () => {
    renderImport()
    fireEvent.change(screen.getByLabelText('Source method'), { target: { value: 'path' } })
    fireEvent.change(screen.getByLabelText(/Server paths, one per line/), { target: { value: '/imports/one.raw\n/imports/two.d\n/imports/one.raw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add paths' }))
    expect(screen.getByLabelText('Run name 2')).toHaveValue('two')
    fireEvent.click(screen.getByRole('button', { name: 'Remove /imports/one.raw' }))
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 run' }))
    expect(await screen.findByText('1 of 1 imported')).toBeInTheDocument()
    expect(api.importRun).toHaveBeenCalledWith(expect.objectContaining({ sourcePath: '/imports/two.d', file: undefined }))
  })

  it('preserves the editable queue when destination preparation fails', async () => {
    vi.mocked(api.prepareImport).mockRejectedValueOnce(new Error('Project unavailable'))
    renderImport()
    addFiles('one.mgf')
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 run' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Project unavailable')
    expect(api.importRun).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByLabelText('Run name 1')).not.toHaveAttribute('readonly'))
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 run' }))
    expect(await screen.findByText('1 of 1 imported')).toBeInTheDocument()
  })
})
