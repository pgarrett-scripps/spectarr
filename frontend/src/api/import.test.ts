import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, clearAccessToken, setAccessToken } from './client'

class UploadRequest {
  static latest: UploadRequest
  status = 201
  responseText = '{}'
  upload: { onprogress: (event: { loaded: number, total: number, lengthComputable: boolean }) => void, onload: () => void } = {
    onprogress: () => {}, onload: () => {}
  }
  onload = () => {}
  onerror = () => {}
  onabort = () => {}
  open = vi.fn()
  setRequestHeader = vi.fn()
  send = vi.fn()
  constructor() { UploadRequest.latest = this }
}

afterEach(() => {
  clearAccessToken()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const destination = { projectId: 'project-1', experimentId: 'experiment-1' }
const values = { projectName: 'Study', experimentName: 'Cohort', sampleName: 'QC', runName: 'one', destination, idempotencyKey: 'import-key' }

function importFetch() {
  const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(
    url.includes('/samples') ? [{ id: 'sample-1', name: 'QC' }] : { id: 'run-1' }
  ), { status: 200 })))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('import API', () => {
  it('uses the selected project ID and recovers a concurrent experiment creation', async () => {
    let lists = 0
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/v1/projects/project-1') return Promise.resolve(new Response(JSON.stringify({ id: 'project-1' })))
      if (init?.method === 'POST') return Promise.resolve(new Response(JSON.stringify({ detail: 'Already exists' }), { status: 409 }))
      lists += 1
      return Promise.resolve(new Response(JSON.stringify(lists === 1 ? [] : [{ id: 'experiment-1', name: 'Cohort' }])))
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.prepareImport({ projectId: 'project-1', projectName: 'Study', experimentName: 'Cohort' })).resolves.toEqual(destination)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/project-1')
    expect(lists).toBe(2)
  })

  it('sends authenticated uploads with progress and the same key as run creation', async () => {
    const fetchMock = importFetch()
    vi.stubGlobal('XMLHttpRequest', UploadRequest)
    setAccessToken('session-token')
    const progress = vi.fn()
    const promise = api.importRun({ ...values, file: new File(['test'], 'one.mzML.gz'), onProgress: progress })
    await vi.waitFor(() => expect(UploadRequest.latest.send).toHaveBeenCalled())
    const xhr = UploadRequest.latest
    expect(xhr.open).toHaveBeenCalledWith('POST', '/api/v1/runs/run-1/artifacts/upload')
    expect(xhr.setRequestHeader).toHaveBeenCalledWith('Authorization', 'Bearer session-token')
    expect(xhr.setRequestHeader).toHaveBeenCalledWith('Idempotency-Key', 'import-key')
    expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/runs', expect.objectContaining({
      headers: expect.objectContaining({ 'Idempotency-Key': 'import-key' }),
      body: JSON.stringify({ experiment_id: 'experiment-1', sample_id: 'sample-1', name: 'one', source_class: 'open' })
    }))
    xhr.upload.onprogress({ loaded: 5, total: 10, lengthComputable: true })
    expect(progress).toHaveBeenLastCalledWith({ phase: 'Uploading', percent: 50 })
    xhr.upload.onload()
    expect(progress).toHaveBeenLastCalledWith({ phase: 'Registering source' })
    xhr.onload()
    await expect(promise).resolves.toEqual({ id: 'run-1', projectId: 'project-1' })
  })

  it('surfaces an upload rejection and expires an invalid browser session', async () => {
    importFetch()
    vi.stubGlobal('XMLHttpRequest', UploadRequest)
    setAccessToken('expired-session')
    const previousUpload = UploadRequest.latest
    const promise = api.importRun({ ...values, file: new File(['test'], 'one.mgf') })
    const rejection = expect(promise).rejects.toMatchObject({ status: 401, message: 'Session expired' })
    await vi.waitFor(() => {
      expect(UploadRequest.latest).not.toBe(previousUpload)
      expect(UploadRequest.latest.send).toHaveBeenCalled()
    })
    UploadRequest.latest.status = 401
    UploadRequest.latest.responseText = JSON.stringify({ detail: 'Session expired' })
    UploadRequest.latest.onload()
    await rejection
    expect(sessionStorage.getItem('spectarr_access_token')).toBeNull()
  })

  it('keeps the key when retrying a server path and rejects missing sources before creating records', async () => {
    const fetchMock = importFetch()
    await api.importRun({ ...values, sourcePath: '/imports/one.d' })
    expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/runs/run-1/artifacts/import', expect.objectContaining({
      headers: expect.objectContaining({ 'Idempotency-Key': 'import-key' }),
      body: JSON.stringify({ source_path: '/imports/one.d', role: 'source' })
    }))
    fetchMock.mockClear()
    await expect(api.importRun(values)).rejects.toThrow('Choose a file or a server path')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
