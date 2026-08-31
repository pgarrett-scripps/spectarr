import type { Run } from './types'

export type RunDetailTab = 'summary' | 'spectra' | 'files' | 'processing' | 'provenance'

export function projectRunsPath(projectId: string, experimentId?: string) {
  const base = `/projects/${encodeURIComponent(projectId)}/runs`
  return experimentId ? `${base}?experiment=${encodeURIComponent(experimentId)}` : base
}

export function importRunPath(projectId?: string) {
  return projectId ? `/runs/import?project=${encodeURIComponent(projectId)}` : '/runs/import'
}

export function runPath(run: Pick<Run, 'id' | 'projectId'>, tab: RunDetailTab = 'summary') {
  const suffix = tab === 'summary' ? '' : `/${tab}`
  return run.projectId
    ? `/projects/${encodeURIComponent(run.projectId)}/runs/${encodeURIComponent(run.id)}${suffix}`
    : `/runs/${encodeURIComponent(run.id)}${suffix}`
}
