import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'


afterEach(() => {
  vi.unstubAllGlobals()
})

describe('spectrum API', () => {
  it('requests a zero-based MS-level position', async () => {
    const payload = {
      schema: 'spxtacular.spectrum',
      schema_version: 1,
      kind: 'msn_spectrum',
      arrays: { mz: [100], intensity: [20], charge: null, im: null, iso_score: null },
      metadata: { spectrum_type: 'centroid', denoised: null, normalized: null, deconvolution: null }
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await api.spectrum('artifact / one', { msLevel: 2, index: 3 })

    expect(result.schema).toBe('spxtacular.spectrum')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/artifacts/artifact%20%2F%20one/spectrum?ms_level=2&index=3',
      expect.objectContaining({ headers: expect.objectContaining({ Accept: 'application/json' }) })
    )
  })

  it('requests a paged spectrum catalog search', async () => {
    const payload = {
      schema: 'spectarr.spectrum-catalog',
      schema_version: 1,
      total: 1,
      offset: 0,
      limit: 12,
      match_index: 0,
      items: []
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await api.spectra('artifact one', { msLevel: 2, limit: 12, rtSeconds: 90 })

    expect(result.schema).toBe('spectarr.spectrum-catalog')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/artifacts/artifact%20one/spectra?ms_level=2&offset=0&limit=12&rt_seconds=90',
      expect.objectContaining({ headers: expect.objectContaining({ Accept: 'application/json' }) })
    )
  })

  it('posts an indexed spectrum query and reads a catalog row', async () => {
    const page = {
      schema: 'spectarr.spectrum-catalog',
      schema_version: 2,
      strategy: 'persistent',
      total: 1,
      limit: 50,
      next_cursor: null,
      items: []
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(page), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ schema: 'spxtacular.spectrum', schema_version: 1 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await api.querySpectra('artifact one', {
      msLevels: [2],
      retentionTimeMin: 90,
      precursorMzMin: 500,
      sort: 'precursor_mz',
      direction: 'desc'
    })
    await api.catalogSpectrum('artifact one', 'entry/7')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/artifacts/artifact%20one/spectra/query')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual(expect.objectContaining({
      ms_levels: [2],
      retention_time_min: 90,
      precursor_mz_min: 500,
      sort: 'precursor_mz',
      direction: 'desc',
      limit: 50
    }))
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/artifacts/artifact%20one/spectra/entry%2F7')
  })
})
