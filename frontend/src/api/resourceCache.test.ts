import { afterEach, expect, it, vi } from 'vitest'
import { clearAccessToken, request, setAccessToken } from './client'
import { clearResourceCache, readResourceCache, resourceCacheGeneration, writeResourceCache } from './resourceCache'

afterEach(() => {
  clearAccessToken()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('expires entries and bounds the number of saved pages', () => {
  vi.spyOn(Date, 'now').mockReturnValue(0)
  const generation = resourceCacheGeneration()
  Array.from({ length: 51 }, (_, index) => writeResourceCache(String(index), index, generation))
  expect(readResourceCache('0')).toBeUndefined()
  expect(readResourceCache('1')).toEqual({ data: 1 })
  vi.mocked(Date.now).mockReturnValue(60_000)
  expect(readResourceCache('1')).toBeUndefined()
})

it('prevents requests started before invalidation from repopulating the cache', () => {
  const generation = resourceCacheGeneration()
  clearResourceCache()
  writeResourceCache('projects', ['stale'], generation)
  expect(readResourceCache('projects')).toBeUndefined()
})

it('clears cached pages on account changes', () => {
  writeResourceCache('projects', ['private'], resourceCacheGeneration())
  setAccessToken('another-account')
  expect(readResourceCache('projects')).toBeUndefined()
  writeResourceCache('projects', ['private'], resourceCacheGeneration())
  clearAccessToken()
  expect(readResourceCache('projects')).toBeUndefined()
})

it('invalidates before and after a mutation even when its response fails', async () => {
  let complete: (value: Response) => void = () => undefined
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { complete = resolve })))
  writeResourceCache('projects', ['before'], resourceCacheGeneration())
  const pending = request('/projects', { method: 'POST' })
  expect(readResourceCache('projects')).toBeUndefined()
  writeResourceCache('projects', ['during'], resourceCacheGeneration())
  complete(new Response('{}', { status: 500 }))
  await expect(pending).rejects.toThrow()
  expect(readResourceCache('projects')).toBeUndefined()
})
