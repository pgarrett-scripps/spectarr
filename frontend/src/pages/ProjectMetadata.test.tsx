import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { SdrfDocument } from '../types'
import { ProjectMetadata } from './ProjectMetadata'

vi.mock('../api/client', async importOriginal => ({ ...await importOriginal<typeof import('../api/client')>(), api: {
  project: vi.fn(), sdrfTemplates: vi.fn(), submissionPreview: vi.fn(), projectSdrf: vi.fn(), saveProjectSdrf: vi.fn(), validateProjectSdrf: vi.fn(), downloadProjectSdrf: vi.fn()
} }))
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }))
const document: SdrfDocument = {
  id: 'sdrf', projectId: 'p', specificationVersion: '1', templates: [], revision: 1, status: 'draft', createdAt: '', updatedAt: '',
  columns: ['source name', 'characteristics[organism]', 'comment[data file]'],
  rows: [{ position: 0, runId: 'run', values: ['sample', 'human', 'run.raw'] }]
}
beforeEach(() => {
  vi.mocked(api.project).mockResolvedValue({ id: 'p', name: 'Project', runCount: 1, sizeBytes: 0, updatedAt: '', metadata: {} })
  vi.mocked(api.sdrfTemplates).mockResolvedValue([])
  vi.mocked(api.submissionPreview).mockResolvedValue({ projectId: 'p', sourceCount: 1, derivativeCount: 0, totalBytes: 0, sdrfStatus: 'draft', mappedRows: 1, unmappedRows: 0, ready: false })
  vi.mocked(api.projectSdrf).mockResolvedValue(structuredClone(document))
  vi.mocked(api.saveProjectSdrf).mockImplementation(async (_, value) => ({ ...document, ...value, revision: 2 }))
  vi.mocked(api.validateProjectSdrf).mockResolvedValue({ valid: true, engine: 'validator', ontology: false, errorCount: 0, warningCount: 0, messages: [] })
})
afterEach(() => { cleanup()
  vi.restoreAllMocks()
  vi.resetAllMocks() })
async function setup() {
  render(<MemoryRouter initialEntries={['/projects/p/metadata']}><Routes><Route path="/projects/:projectId/metadata" element={<ProjectMetadata />} /></Routes></MemoryRouter>)
  await screen.findByLabelText('Row 1, source name, column 1')
}

it('saves the complete mapped document before validating edits in the focused editor', async () => {
  await setup()
  expect(screen.getByLabelText('Project name')).not.toBeVisible()
  fireEvent.change(screen.getByLabelText('Row 1, source name, column 1'), { target: { value: 'updated sample' } })
  fireEvent.click(screen.getByRole('button', { name: 'Validate now' }))
  await screen.findByText('SDRF validation passed')
  expect(api.saveProjectSdrf).toHaveBeenCalledWith('p', expect.objectContaining({ rows: [{ position: 0, runId: 'run', values: ['updated sample', 'human', 'run.raw'] }] }))
  expect(vi.mocked(api.saveProjectSdrf).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.validateProjectSdrf).mock.invocationCallOrder[0])
})

it('keeps the draft when saving fails and does not validate stale server data', async () => {
  await setup()
  vi.mocked(api.saveProjectSdrf).mockRejectedValue(new Error('Save unavailable'))
  fireEvent.change(screen.getByLabelText('Row 1, source name, column 1'), { target: { value: 'unsaved sample' } })
  fireEvent.click(screen.getByRole('button', { name: 'Validate now' }))
  await screen.findByText('Save unavailable')
  expect(api.validateProjectSdrf).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Row 1, source name, column 1')).toHaveValue('unsaved sample')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save table' })).toBeEnabled())
})
