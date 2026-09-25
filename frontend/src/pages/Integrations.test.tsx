import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { Integrations } from './Integrations'

vi.mock('../api/client', () => ({ api: { systemHealth: vi.fn(), webhooks: vi.fn(), webhookDeliveries: vi.fn() } }))
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { role: 'viewer' } }) }))
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.resetAllMocks()
})

it('copies the configured MCP endpoint for a custom deployment', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  vi.mocked(api.systemHealth).mockResolvedValue({ status: 'ok', version: '0.3.0', database: 'ok', storage: 'ok', mcpPublicUrl: 'https://spectarr.example/agent/mcp' })
  render(<Integrations />)
  expect(await screen.findByText('https://spectarr.example/agent/mcp')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Copy MCP URL' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('https://spectarr.example/agent/mcp'))
})

it('does not copy an assumed endpoint when health configuration cannot load', async () => {
  vi.mocked(api.systemHealth).mockRejectedValue(new Error('Server unavailable'))
  render(<Integrations />)
  await screen.findByText('API health: Server unavailable')
  expect(screen.getByRole('button', { name: 'Copy MCP URL' })).toBeDisabled()
})
