import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useResource } from './useResource'

afterEach(cleanup)

describe('useResource', () => {
  it('resets stale data and reloads when its route key changes', async () => {
    const pending = new Map<string, (value: string) => void>()
    const load = vi.fn((id: string) => new Promise<string>(resolve => pending.set(id, resolve)))

    function Harness({ id }: { id: string }) {
      const resource = useResource(() => load(id), 'empty', id)
      return <div>{resource.loading ? 'loading' : 'ready'}:{resource.data}</div>
    }

    const view = render(<Harness id="run-1" />)
    await waitFor(() => expect(load).toHaveBeenCalledWith('run-1'))
    await act(async () => pending.get('run-1')?.('first run'))
    expect(await screen.findByText('ready:first run')).toBeInTheDocument()

    view.rerender(<Harness id="run-2" />)
    expect(await screen.findByText('loading:empty')).toBeInTheDocument()
    await waitFor(() => expect(load).toHaveBeenCalledWith('run-2'))
    await act(async () => pending.get('run-2')?.('second run'))
    expect(await screen.findByText('ready:second run')).toBeInTheDocument()
  })
})
