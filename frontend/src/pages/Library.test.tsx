import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { Project } from '../types'
import { Projects } from './Library'

vi.mock('../api/client', async importOriginal => ({ ...await importOriginal<typeof import('../api/client')>(), api: { projects: vi.fn() } }))
const auth = vi.hoisted(() => ({ user: { role: 'admin' } }))
vi.mock('../auth/AuthContext', () => ({ useAuth: () => auth }))

const projects: Project[] = [
  { id: 'plasma', name: 'Plasma proteomics', description: 'Longitudinal treatment cohort', runCount: 8, sizeBytes: 2048, updatedAt: '2026-09-22T12:00:00Z', metadata: {} },
  { id: 'yeast', name: 'Yeast reference', description: 'Instrument performance', runCount: 3, sizeBytes: 1024, updatedAt: '2026-09-24T12:00:00Z', metadata: {} },
  { id: 'inbox', name: 'Instrument Inbox', systemKey: 'inbox', runCount: 5, sizeBytes: 0, updatedAt: '2026-09-24T12:00:00Z', metadata: {} }
]

beforeEach(() => {
  auth.user.role = 'admin'
  vi.mocked(api.projects).mockResolvedValue(projects)
})
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
async function setup() {
  render(<MemoryRouter><Projects /></MemoryRouter>)
  return screen.findByRole('table', { name: 'Research projects' })
}

it('finds projects by description and lets a user recover from an empty search', async () => {
  await setup()
  expect(screen.queryByRole('link', { name: 'Instrument Inbox' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Search projects' }), { target: { value: ' INSTRUMENT ' } })
  expect(screen.getByRole('link', { name: 'Yeast reference' })).toHaveAttribute('href', '/projects/yeast/runs')
  expect(screen.queryByRole('link', { name: 'Plasma proteomics' })).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('1 project')
  fireEvent.change(screen.getByRole('textbox', { name: 'Search projects' }), { target: { value: 'missing cohort' } })
  expect(screen.getByRole('heading', { name: 'No matching projects' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
  expect(screen.getByRole('status')).toHaveTextContent('2 projects')
})

it('sorts by recency, project name, and run count without mutating the cached response', async () => {
  const table = await setup()
  const names = () => within(table).getAllByRole('link').map(link => link.textContent)
  expect(names()).toEqual(['Yeast reference', 'Plasma proteomics'])
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort projects' }), { target: { value: 'name' } })
  expect(names()).toEqual(['Plasma proteomics', 'Yeast reference'])
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort projects' }), { target: { value: 'runs' } })
  expect(names()).toEqual(['Plasma proteomics', 'Yeast reference'])
  expect(projects.map(project => project.id)).toEqual(['plasma', 'yeast', 'inbox'])
})

it('keeps browsing available to viewers without project administration actions', async () => {
  auth.user.role = 'viewer'
  await setup()
  expect(screen.getByRole('link', { name: 'Plasma proteomics' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'New project' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Manage members/ })).not.toBeInTheDocument()
})

it('does not describe a failed project request as an empty library', async () => {
  vi.mocked(api.projects).mockRejectedValueOnce(new Error('Project list unavailable'))
  render(<MemoryRouter><Projects /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Project list unavailable')
  expect(screen.queryByRole('heading', { name: 'No projects yet' })).not.toBeInTheDocument()
})
