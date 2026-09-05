import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { BackupStatus } from '../types'
import { BackupSettings } from './BackupSettings'

vi.mock('../api/client', () => ({ api: { backups: vi.fn(), runBackup: vi.fn(), checkBackupRestore: vi.fn(), saveBackupPolicy: vi.fn() } }))

const base: BackupStatus = {
  instance_id: 'instance-1', policy: { enabled: false, every_days: 1, time_utc: '03:00', keep_last: 3, restore_every_days: 30 },
  status: 'idle', operation: null, configured: true, destination: '/backups', destination_available: true,
  destination_error: null, same_filesystem: false, free_bytes: 1024 ** 3, next_backup_at: null,
  last_attempt_at: null, last_success_at: null, last_restore_at: null, last_error: null, last_restore_error: null,
  latest_backup: null, history: [], restore_mode: false,
}

beforeEach(() => {
  vi.mocked(api.backups).mockResolvedValue(base)
  vi.mocked(api.runBackup).mockResolvedValue({ ...base, status: 'queued' })
  vi.mocked(api.checkBackupRestore).mockResolvedValue({ ...base, status: 'queued' })
  vi.mocked(api.saveBackupPolicy).mockResolvedValue(base)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('backup settings', () => {
  it('saves an explicit schedule and retention policy', async () => {
    render(<BackupSettings />)
    await screen.findByText('No verified backup yet')
    fireEvent.change(screen.getByLabelText('Automatic backups'), { target: { value: 'true' } })
    fireEvent.change(screen.getByLabelText('Backup time (UTC)'), { target: { value: '04:30' } })
    fireEvent.change(screen.getByLabelText('Keep newest backups'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save backup policy' }))
    expect(api.saveBackupPolicy).toHaveBeenCalledWith({ ...base.policy, enabled: true, time_utc: '04:30', keep_last: 5 })
  })

  it('queues an on-demand backup and disables duplicate operations', async () => {
    render(<BackupSettings />)
    await screen.findByText('No verified backup yet')
    vi.mocked(api.backups).mockResolvedValue({ ...base, status: 'queued' })
    fireEvent.click(screen.getByRole('button', { name: 'Back up now' }))
    expect(await screen.findByText('Queued')).toBeInTheDocument()
    expect(api.runBackup).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Back up now' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save backup policy' })).toBeDisabled()
  })

  it('shows destination and operation failures without losing the last successful backup', async () => {
    vi.mocked(api.backups).mockResolvedValue({ ...base, status: 'failed', last_success_at: '2026-09-04T03:00:00Z', destination_available: false, destination_error: 'Backup mount is missing', last_error: 'Disk full', same_filesystem: true })
    render(<BackupSettings />)
    expect(await screen.findByText('Backup needs attention')).toBeInTheDocument()
    expect(screen.getByText('Backup mount is missing')).toBeInTheDocument()
    expect(screen.getByText('Disk full')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back up now' })).toBeDisabled()
    expect(screen.getByText(/shares a filesystem/)).toBeInTheDocument()
  })

  it('preserves unsaved policy edits across status polling', async () => {
    vi.useFakeTimers()
    render(<BackupSettings />)
    await act(async () => {})
    fireEvent.change(screen.getByLabelText('Keep newest backups'), { target: { value: '7' } })
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(api.backups).toHaveBeenCalledTimes(2)
    expect(screen.getByLabelText('Keep newest backups')).toHaveValue(7)
  })

  it('requests a restore check for a verified snapshot', async () => {
    vi.mocked(api.backups).mockResolvedValue({ ...base, history: [{ id: 'backup-one', created_at: '2026-09-04T03:00:00Z', verified_at: '2026-09-04T03:01:00Z', byte_size: 1000, artifact_objects: 2 }] })
    render(<BackupSettings />)
    await screen.findByText('backup-one')
    fireEvent.click(screen.getByRole('button', { name: 'Test latest restore' }))
    expect(api.checkBackupRestore).toHaveBeenCalledOnce()
  })
})
