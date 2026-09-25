import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useResource } from './useResource'
import { clearResourceCache } from './resourceCache'

afterEach(() => {
  cleanup()
  clearResourceCache()
})

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

it('shows a cached page immediately on return and replaces it with fresh data', async () => {
  let complete: (value: string) => void = () => undefined
  const load = vi.fn().mockResolvedValueOnce('cached').mockImplementation(() => new Promise<string>(resolve => { complete = resolve }))
  function Harness() {
    const resource = useResource(load, 'empty', undefined, 'projects')
    return <span>{resource.data}</span>
  }
  const view = render(<Harness />)
  await screen.findByText('cached')
  view.unmount()
  render(<Harness />)
  expect(screen.getByText('cached')).toBeInTheDocument()
  expect(load).toHaveBeenCalledTimes(2)
  await act(async () => complete('fresh'))
  expect(screen.getByText('fresh')).toBeInTheDocument()
})

it('does not mix cached routes or retain a page after access is revoked', async () => {
  const load = vi.fn().mockResolvedValueOnce('first').mockResolvedValueOnce('second')
    .mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))
  function Harness({ id }: { id: string }) {
    const resource = useResource(load, 'empty', id, `run:${id}`)
    return <span>{resource.data}</span>
  }
  const view = render(<Harness id="1" />)
  await screen.findByText('first')
  view.rerender(<Harness id="2" />)
  expect(screen.getByText('empty')).toBeInTheDocument()
  await screen.findByText('second')
  view.rerender(<Harness id="1" />)
  expect(screen.getByText('first')).toBeInTheDocument()
  await screen.findByText('empty')
})

it('keeps loaded results after a failed refresh but clears them for another route', async () => {
  const load = vi.fn().mockResolvedValueOnce('saved').mockRejectedValue(new Error('Offline'))
  function Harness({ id }: { id: string }) {
    const resource = useResource(load, 'empty', id)
    return <><span>{resource.data}</span><span>{resource.error}</span><button onClick={resource.refresh}>Refresh</button></>
  }
  const view = render(<Harness id="first" />)
  await screen.findByText('saved')
  await act(async () => { screen.getByRole('button', { name: 'Refresh' }).click() })
  expect(screen.getByText('Offline')).toBeInTheDocument()
  expect(screen.getByText('saved')).toBeInTheDocument()
  view.rerender(<Harness id="second" />)
  await screen.findByText('Offline')
  expect(screen.getByText('empty')).toBeInTheDocument()
  expect(screen.queryByText('saved')).not.toBeInTheDocument()
})
