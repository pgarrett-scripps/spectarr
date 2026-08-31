import { describe, expect, it } from 'vitest'
import { importRunPath, projectRunsPath, runPath } from './navigation'

describe('project-first navigation', () => {
  it('builds project run routes with optional experiment filters', () => {
    expect(projectRunsPath('project 1')).toBe('/projects/project%201/runs')
    expect(projectRunsPath('project 1', 'experiment/2')).toBe('/projects/project%201/runs?experiment=experiment%2F2')
  })

  it('keeps run detail inside its project when possible', () => {
    const run = { id: 'run/1', projectId: 'project 1' }
    expect(runPath(run)).toBe('/projects/project%201/runs/run%2F1')
    expect(runPath(run, 'spectra')).toBe('/projects/project%201/runs/run%2F1/spectra')
  })

  it('falls back to the global run route for unassigned runs', () => {
    expect(runPath({ id: 'inbox-run', projectId: undefined })).toBe('/runs/inbox-run')
  })

  it('keeps imports in the current project context', () => {
    expect(importRunPath()).toBe('/runs/import')
    expect(importRunPath('project 1')).toBe('/runs/import?project=project%201')
  })
})
