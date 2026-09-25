import { request } from './client'

export type RepositoryFile = {
  id: string
  filename: string
  byte_size: number
  category: string
  supported: boolean
  reason?: string
  previously_imported: boolean
  is_sdrf?: boolean
  checksum: { algorithm: string, value: string } | null
}
export type RepositoryDataset = {
  accession: string
  title: string
  description: string
  doi?: string
  license?: string
  url: string
  files: RepositoryFile[]
  free_bytes: number
  max_file_bytes: number
  download_concurrency?: number
  sdrf_warning?: string | null
}
export type RemoteImport = {
  id: string
  project_id: string
  experiment_id: string
  accession: string
  file_id: string
  filename: string
  run_name: string
  sample_name: string
  state: 'queued' | 'downloading' | 'verifying' | 'registering' | 'succeeded' | 'failed' | 'cancelled'
  byte_size: number
  bytes_received: number
  cancel_requested: boolean
  error?: string
  run_id?: string
  sdrf_rows?: number
}
export type RemoteSelection = { file_id: string, run_name: string, sample_name: string }

export type DownloadSettings = {
  concurrency: number
  default_concurrency: number
  overridden: boolean
  enabled: boolean
  restore_mode: boolean
}

export type RepositorySdrf = {
  file_id: string
  filename: string
  sha256: string
  columns: string[]
  rows: string[][]
  mappings: Record<string, number[]>
  warnings: string[]
}

export const remoteApi = {
  settings: () => request<DownloadSettings>('/settings/downloads'),
  saveSettings: (concurrency: number | null) => request<DownloadSettings>('/settings/downloads', { method: 'PUT', body: JSON.stringify({ concurrency }) }),
  sdrf: (accession: string, fileId: string) => request<RepositorySdrf>(`/repositories/pride/sdrf?${new URLSearchParams({ accession, file_id: fileId })}`),
  lookup: (accession: string, projectId?: string) => request<RepositoryDataset>(`/repositories/pride?${new URLSearchParams({ accession, ...(projectId ? { project_id: projectId } : {}) })}`),
  enqueue: (accession: string, experimentId: string, files: RemoteSelection[], key: string, sdrf?: { file_id: string, sha256: string }) => request<RemoteImport[]>('/remote-imports', {
    method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ accession, experiment_id: experimentId, files, ...(sdrf ? { sdrf_file_id: sdrf.file_id, sdrf_sha256: sdrf.sha256 } : {}) })
  }),
  list: async (projectId?: string): Promise<RemoteImport[]> => {
    const results: RemoteImport[] = []
    while (true) {
      const page = await request<RemoteImport[]>(`/remote-imports?${new URLSearchParams({ limit: '500', offset: String(results.length), ...(projectId ? { project_id: projectId } : {}) })}`)
      results.push(...page)
      if (page.length < 500) return results
    }
  },
  action: (id: string, action: 'cancel' | 'retry') => request<RemoteImport>(`/remote-imports/${encodeURIComponent(id)}/${action}`, { method: 'POST' })
}
