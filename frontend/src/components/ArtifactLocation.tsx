import { useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import type { ArtifactAccess } from '../types'
import { ApiErrorBanner, LoadingState } from './Page'

const availabilityLabels: Record<ArtifactAccess['availability'], string> = {
  available: 'Available in the managed library',
  unmaterialized: 'Library copy is missing. Stored content is available.',
  missing: 'Stored content is missing',
  purged: 'Generated content was reclaimed',
  not_ready: 'File is not ready for use'
}

export function ArtifactLocation({ artifactId }: { artifactId: string }) {
  const [open, setOpen] = useState(false)
  return <details className="artifact-location" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Locate file</summary>
    {open && <LocationDetails artifactId={artifactId} />}
  </details>
}

function LocationDetails({ artifactId }: { artifactId: string }) {
  const resource = useResource<ArtifactAccess | null>(() => api.artifactAccess(artifactId), null, artifactId)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState<string | null>(null)
  const access = resource.data
  const copy = async () => {
    if (!access?.server_path) return
    setCopyError(null)
    try {
      await navigator.clipboard.writeText(access.server_path)
      setCopied(true)
    } catch {
      setCopyError('Could not copy automatically. Select the server path to copy it.')
    }
  }
  return <>
    {resource.loading && !access && <LoadingState label="Checking file location" />}
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    {access && <>
      <dl className="metadata-list">
        <div><dt>Availability</dt><dd>{availabilityLabels[access.availability]}</dd></div>
        <div><dt>Server path</dt><dd className="mono">{access.server_path ?? 'No usable library path'}</dd></div>
        <div><dt>Library root</dt><dd className="mono">{access.library_root}</dd></div>
        <div><dt>File type</dt><dd>{access.is_directory ? 'Directory bundle' : 'Single file'}</dd></div>
        <div><dt>SHA-256 at import</dt><dd className="mono">{access.sha256}</dd></div>
      </dl>
      <p>Server paths may be inside a container. Use your mounted library folder on this PC. Files are read-only. This checks availability, not current checksum integrity.</p>
      {access.is_directory && <p>Open the complete directory in the managed library. Bundle downloads are not supported.</p>}
      {access.server_path && <button className="button button-secondary button-small" onClick={() => void copy()}>{copied ? 'Copied server path' : 'Copy server path'}</button>}
      {copyError && <p role="status">{copyError}</p>}
    </>}
  </>
}
