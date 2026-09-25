import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ProcessingProfile } from '../types'
import { Automation } from './Automation'

vi.mock('../api/client', () => ({ api: { automationRules: vi.fn(), processingProfiles: vi.fn(), projects: vi.fn(), instruments: vi.fn(), createAutomationRule: vi.fn(), updateProcessingProfile: vi.fn() } }))
afterEach(() => { cleanup()
  vi.resetAllMocks() })
const advanced = { kind: 'peak_picking', algorithm: 'cwt', ms_levels: [1], signal_to_noise: 2 }
const profile: ProcessingProfile = { id: 'standard', name: 'Standard mzML', enabled: true, system: true, revision: 1, outputFormat: 'mzML', createdAt: '', updatedAt: '', parameters: { filters: [advanced], mzPrecision: 64, intensityPrecision: 32, compression: 'zlib', indexed: true } }
function setup() {
  vi.mocked(api.automationRules).mockResolvedValue([])
  vi.mocked(api.processingProfiles).mockResolvedValue([profile])
  vi.mocked(api.projects).mockResolvedValue([])
  vi.mocked(api.instruments).mockResolvedValue([])
  render(<Automation />)
}

it('creates a metadata-only rule after all processing profiles are cleared', async () => {
  setup()
  await waitFor(() => expect(screen.getByRole('button', { name: 'New rule' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'New rule' }))
  const dialog = within(screen.getByRole('dialog'))
  fireEvent.change(dialog.getByLabelText('Name'), { target: { value: 'Metadata only' } })
  fireEvent.click(dialog.getByRole('checkbox', { name: /Standard mzML/ }))
  fireEvent.click(dialog.getByRole('button', { name: 'Create and reconcile' }))
  await waitFor(() => expect(api.createAutomationRule).toHaveBeenCalledWith(expect.objectContaining({ extractMetadata: true, profileIds: [] })))
})

it('preserves advanced filters when editing basic profile settings', async () => {
  setup()
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
  const dialog = within(screen.getByRole('dialog'))
  expect(dialog.getByLabelText('Apply vendor peak picking')).not.toBeChecked()
  fireEvent.change(dialog.getByLabelText('Description'), { target: { value: 'Updated description' } })
  fireEvent.click(dialog.getByRole('button', { name: 'Save profile' }))
  await waitFor(() => expect(api.updateProcessingProfile).toHaveBeenCalledWith('standard', expect.objectContaining({ parameters: expect.objectContaining({ filters: [advanced] }) })))
})
