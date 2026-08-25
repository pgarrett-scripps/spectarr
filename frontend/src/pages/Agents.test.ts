import { describe, expect, it } from 'vitest'
import { resolveProjectId } from './agentDestination'

describe('agent destination project selection', () => {
  const scientificProjects = [{ id: 'project-a' }, { id: 'project-b' }]

  it('keeps a valid scientific project', () => {
    expect(resolveProjectId('project-b', scientificProjects)).toBe('project-b')
  })

  it('replaces a system inbox project with the first scientific project', () => {
    expect(resolveProjectId('system-inbox', scientificProjects)).toBe('project-a')
  })
})
