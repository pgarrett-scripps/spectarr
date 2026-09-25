import { request } from './client'

export interface ExternalRoot {
  id: string
  agent_id: string
  label: string
  path: string
  enabled: boolean
  status: string
  freshness: string
  error?: string
}
export interface ExternalLocation {
  id: string
  root_id: string
  root_label: string
  root_path: string
  relative_path: string
  status: string
  root_status: string
  enabled: boolean
  readiness: string
  byte_size: number
  revision_id: string | null
  observed_at: string
  verified_at: string | null
  freshness: string
  root_freshness: string
  agent_freshness: string
  host: string
  sha256?: string
}
export interface ExternalEntry {
  id: string
  name: string
  format: string
  kind: string
  locations: ExternalLocation[]
}
export interface ExternalTask {
  id: string
  kind: string
  state: string
  error?: string
  artifact_id?: string
}
export interface ExternalPage {
  items: ExternalEntry[]
  total: number
  next_cursor: string | null
}
const post = <T,>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
const retryKeys = new Map<string, string>()
export const externalApi = {
  roots: (projectId: string) => request<{ items: ExternalRoot[] }>(`/external-roots?project_id=${encodeURIComponent(projectId)}`),
  search: (projectId: string, query: string, after = '') => request<ExternalPage>(`/external-entries?project_id=${encodeURIComponent(projectId)}&query=${encodeURIComponent(query)}&after=${encodeURIComponent(after)}`),
  register: (body: { project_id: string, agent_id: string, label: string, path: string }) => post<ExternalRoot>('/external-roots', body),
  update: (id: string, enabled: boolean, revalidate = false) => request<ExternalRoot>(`/external-roots/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled, revalidate }) }),
  tasks: (rootId: string) => request<{ items: ExternalTask[] }>(`/external-roots/${rootId}/tasks`),
  queue: async (rootId: string, kind: string, fields = {}) => {
    const body = JSON.stringify({ kind, ...fields })
    const identity = rootId + body
    const key = retryKeys.get(identity) ?? crypto.randomUUID()
    retryKeys.set(identity, key)
    const result = await request(`/external-roots/${rootId}/tasks`, { method: 'POST', body, headers: { 'Idempotency-Key': key } })
    retryKeys.delete(identity)
    return result
  },
  cancel: (id: string) => post(`/external-tasks/${id}/cancel`, {}),
  matches: (id: string) => request<{ items: ExternalEntry[] }>(`/external-entries/${id}/matches`),
  history: (id: string) => request<{ items: { id: string, facts: Record<string, unknown>, created_at: string }[] }>(`/external-entries/${id}/history`),
  relocate: (old: ExternalLocation, candidate: ExternalLocation) => post('/external-relocations', {
    source_location_id: old.id, candidate_location_id: candidate.id,
    source_revision_id: old.revision_id, candidate_revision_id: candidate.revision_id
  })
}
