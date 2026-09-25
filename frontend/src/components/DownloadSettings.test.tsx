import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { remoteApi } from '../api/remote'
import { DownloadSettings } from './DownloadSettings'

vi.mock('../api/remote', () => ({ remoteApi: { settings: vi.fn(), saveSettings: vi.fn() } }))
const defaults = { concurrency: 2, default_concurrency: 2, overridden: false, enabled: true, restore_mode: false }
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('saves a concurrency preference and restores the server default', async () => {
  vi.mocked(remoteApi.settings).mockResolvedValue(defaults)
  vi.mocked(remoteApi.saveSettings).mockResolvedValueOnce({ ...defaults, concurrency: 4, overridden: true }).mockResolvedValueOnce(defaults)
  render(<DownloadSettings />)
  fireEvent.change(await screen.findByLabelText('Concurrent downloads'), { target: { value: '4' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save download settings' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Download settings saved')
  expect(remoteApi.saveSettings).toHaveBeenCalledWith(4)
  fireEvent.click(screen.getByRole('button', { name: 'Use server default' }))
  await waitFor(() => expect(screen.getByLabelText('Concurrent downloads')).toHaveValue('2'))
  expect(remoteApi.saveSettings).toHaveBeenLastCalledWith(null)
})

it('retains an unsaved choice when saving fails', async () => {
  vi.mocked(remoteApi.settings).mockResolvedValue(defaults)
  vi.mocked(remoteApi.saveSettings).mockRejectedValue(new Error('Storage maintenance is in progress'))
  render(<DownloadSettings />)
  fireEvent.change(await screen.findByLabelText('Concurrent downloads'), { target: { value: '3' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save download settings' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Storage maintenance')
  expect(screen.getByLabelText('Concurrent downloads')).toHaveValue('3')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('disables editing during restore verification', async () => {
  vi.mocked(remoteApi.settings).mockResolvedValue({ ...defaults, restore_mode: true, enabled: false })
  render(<DownloadSettings />)
  expect(await screen.findByLabelText('Concurrent downloads')).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Save download settings' })).toBeDisabled()
})
