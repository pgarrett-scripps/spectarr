import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { OnlineImport } from './OnlineImport'
import { remoteApi, type RemoteImport, type RepositoryDataset } from '../api/remote'
import { api } from '../api/client'

vi.mock('../api/remote', () => ({ remoteApi: { lookup: vi.fn(), enqueue: vi.fn(), list: vi.fn(), action: vi.fn(), sdrf: vi.fn() } }))
vi.mock('../api/client', () => ({ api: { prepareImport: vi.fn() } }))
const dataset: RepositoryDataset = {
  accession: 'PXD000001', title: 'Public test study', description: 'Test acquisition',
  url: 'https://www.ebi.ac.uk/pride/archive/projects/PXD000001', free_bytes: 1e9, max_file_bytes: 1e8,
  files: [
    { id: 'f1', filename: 'test.mgf', byte_size: 100, category: 'PEAK', supported: true, previously_imported: false, checksum: null },
    { id: 'f2', filename: 'vendor.d.zip', byte_size: 200, category: 'RAW', supported: false, previously_imported: false, checksum: null, reason: 'Unsupported archive' }
  ]
}
const item: RemoteImport = {
  id: 'i1', accession: 'PXD000001', project_id: 'p1', experiment_id: 'e1', file_id: 'f1',
  filename: 'test.mgf', run_name: 'test', sample_name: 'test', state: 'queued', byte_size: 100, bytes_received: 0, cancel_requested: false
}
const renderImport = () => render(<MemoryRouter><OnlineImport projectId="p1" projectName="Study" experimentName="Experiment" experiments={[]} onProjectName={vi.fn()} onExperimentName={vi.fn()} /></MemoryRouter>)
beforeEach(() => {
  vi.mocked(remoteApi.list).mockResolvedValue([])
  vi.mocked(remoteApi.lookup).mockResolvedValue(dataset)
  vi.mocked(remoteApi.enqueue).mockResolvedValue([item])
  vi.mocked(api.prepareImport).mockResolvedValue({ projectId: 'p1', experimentId: 'e1' })
})
afterEach(() => { cleanup()
  vi.resetAllMocks()
})

it('looks up files, rejects archives, and queues reviewed names', async () => {
  renderImport()
  fireEvent.change(screen.getByLabelText('PRIDE accession or project URL'), { target: { value: 'PXD000001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Look up dataset' }))
  expect(await screen.findByText('Public test study')).toBeInTheDocument()
  expect(screen.getByLabelText('Select vendor.d.zip')).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Select test.mgf'))
  fireEvent.change(screen.getByLabelText('Run name for test.mgf'), { target: { value: 'renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Download 1 file' }))
  await waitFor(() => expect(remoteApi.enqueue).toHaveBeenCalledWith('PXD000001', 'e1', [{ file_id: 'f1', run_name: 'renamed', sample_name: 'test' }], expect.stringMatching(/^[0-9a-f-]{36}$/), undefined))
  expect(await screen.findByText(/1 download queued/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel test' })).toBeInTheDocument()
})

it('reconnects to persisted downloads after remount and permits retry', async () => {
  vi.mocked(remoteApi.list).mockResolvedValue([{ ...item, state: 'failed', error: 'Connection interrupted', bytes_received: 50 }])
  const first = renderImport()
  expect(await screen.findByText('Connection interrupted')).toBeInTheDocument()
  first.unmount()
  renderImport()
  const retry = await screen.findByRole('button', { name: 'Retry test' })
  vi.mocked(remoteApi.action).mockResolvedValue(item)
  fireEvent.click(retry)
  await waitFor(() => expect(remoteApi.action).toHaveBeenCalledWith('i1', 'retry'))
  expect(await screen.findByRole('button', { name: 'Cancel test' })).toBeInTheDocument()
})

it('reuses the request key after an uncertain enqueue response', async () => {
  vi.mocked(remoteApi.enqueue).mockRejectedValueOnce(new Error('Response lost')).mockResolvedValueOnce([item])
  renderImport()
  fireEvent.change(screen.getByLabelText('PRIDE accession or project URL'), { target: { value: 'PXD000001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Look up dataset' }))
  fireEvent.click(await screen.findByLabelText('Select test.mgf'))
  fireEvent.click(screen.getByRole('button', { name: 'Download 1 file' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Response lost')
  fireEvent.click(screen.getByRole('button', { name: 'Download 1 file' }))
  await waitFor(() => expect(remoteApi.enqueue).toHaveBeenCalledTimes(2))
  expect(vi.mocked(remoteApi.enqueue).mock.calls[0][3]).toBe(vi.mocked(remoteApi.enqueue).mock.calls[1][3])
})


it('previews SDRF mappings and includes the reviewed checksum with the selection', async () => {
  vi.mocked(remoteApi.lookup).mockResolvedValue({ ...dataset, download_concurrency: 2, files: [...dataset.files,
    { id: 's1', filename: 'sdrf.tsv', byte_size: 200, category: 'OTHER', supported: false, is_sdrf: true, previously_imported: false, checksum: null }
  ] })
  vi.mocked(remoteApi.sdrf).mockResolvedValue({ file_id: 's1', filename: 'sdrf.tsv', sha256: 'a'.repeat(64),
    columns: ['source name', 'comment[data file]', 'comment[label]'], rows: [['control', 'test.mgf', 'TMT126'], ['treated', 'test.mgf', 'TMT127N']],
    mappings: { f1: [0, 1] }, warnings: [] })
  renderImport()
  fireEvent.change(screen.getByLabelText('PRIDE accession or project URL'), { target: { value: 'PXD000001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Look up dataset' }))
  expect(await screen.findByLabelText('Import SDRF metadata for selected acquisitions')).toBeChecked()
  fireEvent.click(screen.getByLabelText('Select test.mgf'))
  expect(screen.getByText('control, treated')).toBeInTheDocument()
  expect(screen.getByText('From SDRF (2 rows)')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Download 1 file' }))
  await waitFor(() => expect(remoteApi.enqueue).toHaveBeenCalledWith('PXD000001', 'e1', expect.any(Array), expect.any(String), { file_id: 's1', sha256: 'a'.repeat(64) }))
})

it('allows importing acquisitions when SDRF retrieval fails', async () => {
  vi.mocked(remoteApi.lookup).mockResolvedValue({ ...dataset, files: [...dataset.files,
    { id: 's1', filename: 'sdrf.tsv', byte_size: 200, category: 'OTHER', supported: false, is_sdrf: true, previously_imported: false, checksum: null }
  ] })
  vi.mocked(remoteApi.sdrf).mockRejectedValue(new Error('SDRF unavailable'))
  renderImport()
  fireEvent.change(screen.getByLabelText('PRIDE accession or project URL'), { target: { value: 'PXD000001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Look up dataset' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('SDRF unavailable')
  fireEvent.click(screen.getByLabelText('Select test.mgf'))
  expect(screen.getByLabelText('Sample for test.mgf')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Download 1 file' }))
  await waitFor(() => expect(remoteApi.enqueue).toHaveBeenCalledWith('PXD000001', 'e1', expect.any(Array), expect.any(String), undefined))
})

it('searches filenames and bulk selects supported matches while preserving edited names', async () => {
  vi.mocked(remoteApi.lookup).mockResolvedValue({ ...dataset, files: [...dataset.files,
    { ...dataset.files[0], id: 'f3', filename: 'second.raw', category: 'RAW' }
  ] })
  renderImport()
  fireEvent.change(screen.getByLabelText('PRIDE accession or project URL'), { target: { value: 'PXD000001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Look up dataset' }))
  fireEvent.click(await screen.findByLabelText('Select test.mgf'))
  fireEvent.change(screen.getByLabelText('Run name for test.mgf'), { target: { value: 'custom run' } })
  fireEvent.change(screen.getByLabelText('Search filenames'), { target: { value: 'SECOND' } })
  expect(screen.queryByLabelText('Select test.mgf')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Select matching files' }))
  fireEvent.change(screen.getByLabelText('Search filenames'), { target: { value: '' } })
  expect(screen.getByLabelText('Run name for test.mgf')).toHaveValue('custom run')
  expect(screen.getByLabelText('Select vendor.d.zip')).not.toBeChecked()
  expect(screen.getByLabelText('Select second.raw')).toBeChecked()
  fireEvent.change(screen.getByLabelText('File filter'), { target: { value: 'raw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Clear matching selection' }))
  fireEvent.click(screen.getByRole('button', { name: 'Download 1 file' }))
  await waitFor(() => expect(remoteApi.enqueue).toHaveBeenCalledWith('PXD000001', 'e1', [{ file_id: 'f1', run_name: 'custom run', sample_name: 'test' }], expect.any(String), undefined))
})

it('limits bulk selection to 500 and includes matches beyond the first rendered page', async () => {
  vi.mocked(remoteApi.lookup).mockResolvedValue({ ...dataset, files: Array.from({ length: 501 }, (_, index) => ({
    ...dataset.files[0], id: `f${index}`, filename: `${index < 500 ? 'sample' : 'extra'}-${String(index).padStart(3, '0')}.mgf`
  })) })
  renderImport()
  fireEvent.change(screen.getByLabelText('PRIDE accession or project URL'), { target: { value: 'PXD000001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Look up dataset' }))
  expect(await screen.findByRole('button', { name: 'Select matching files' })).toBeDisabled()
  expect(screen.queryByLabelText('Select sample-150.mgf')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Show 100 more files' }))
  expect(screen.getByLabelText('Select sample-150.mgf')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Search filenames'), { target: { value: 'sample-' } })
  fireEvent.click(screen.getByRole('button', { name: 'Select matching files' }))
  fireEvent.change(screen.getByLabelText('Search filenames'), { target: { value: 'extra' } })
  expect(screen.getByLabelText('Select extra-500.mgf')).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Download 500 files' }))
  await waitFor(() => expect(remoteApi.enqueue).toHaveBeenCalled())
  expect(vi.mocked(remoteApi.enqueue).mock.calls[0][2]).toHaveLength(500)
  expect(vi.mocked(remoteApi.enqueue).mock.calls[0][2][499].file_id).toBe('f499')
})
