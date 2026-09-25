import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ArtifactAccess } from '../types'
import { ArtifactLocation } from './ArtifactLocation'

vi.mock('../api/client', () => ({ api: { artifactAccess: vi.fn() } }))

const access: ArtifactAccess = {
  artifact_id: 'artifact-1',
  availability: 'available',
  is_directory: false,
  checked_at: '2026-09-24T12:00:00Z',
  path_scope: 'api_server',
  library_root: '/data/storage/library',
  library_relative_path: 'project/raw/run.raw',
  server_path: '/data/storage/library/project/raw/run.raw',
  download_url: '/api/v1/artifacts/artifact-1/download',
  sha256: 'abc123',
  integrity: 'checksum_recorded_not_reverified',
  usage_note: 'Read-only server path'
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function openLocation() {
  const rendered = render(<ArtifactLocation artifactId="artifact-1" />)
  const details = rendered.container.querySelector('details')!
  details.open = true
  fireEvent(details, new Event('toggle'))
}

describe('file locations', () => {
  it('checks storage only when opened and copies an explicitly labeled server path', async () => {
    vi.mocked(api.artifactAccess).mockResolvedValue(access)
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const rendered = render(<ArtifactLocation artifactId="artifact-1" />)
    expect(api.artifactAccess).not.toHaveBeenCalled()
    const details = rendered.container.querySelector('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    expect(await screen.findByText(access.server_path!)).toBeInTheDocument()
    expect(screen.getByText(/Server paths may be inside a container/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Copy server path' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(access.server_path))
    expect(await screen.findByRole('button', { name: 'Copied server path' })).toBeInTheDocument()
  })

  it('explains bundles and avoids offering a nonexistent path', async () => {
    vi.mocked(api.artifactAccess).mockResolvedValue({ ...access, is_directory: true, availability: 'missing', server_path: null, download_url: null })
    openLocation()
    expect(await screen.findByText('Stored content is missing')).toBeInTheDocument()
    expect(screen.getByText(/Bundle downloads are not supported/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy server path' })).not.toBeInTheDocument()
  })

  it('shows an API error without inventing a path', async () => {
    vi.mocked(api.artifactAccess).mockRejectedValue(new Error('File lookup failed'))
    openLocation()
    expect(await screen.findByText('File lookup failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy server path' })).not.toBeInTheDocument()
  })
})
