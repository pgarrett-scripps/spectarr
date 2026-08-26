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
})
