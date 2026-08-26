import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, clearAccessToken, downloadArtifact, getAccessToken, request, setAccessToken } from './client'

describe('API client', () => {
  afterEach(() => {
    clearAccessToken()
    vi.restoreAllMocks()
  })

  it('returns parsed JSON for successful requests', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 })))
    await expect(request<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
  })

  it('discovers local authentication mode', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      mode: 'local',
      local_user: 'admin',
      allow_remote_no_auth: false
    }), { status: 200 })))
    await expect(api.authConfiguration()).resolves.toEqual({
      mode: 'local',
      localUser: 'admin',
      allowRemoteNoAuth: false
    })
  })

  it('uses the API detail for errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Missing run' }), { status: 404 })))
    await expect(request('/runs/missing')).rejects.toEqual(new ApiError(404, 'Missing run'))
  })

  it('sends the bearer token when authenticated', async () => {
    setAccessToken('secret-token')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await request('/auth/me')
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer secret-token' })
    }))
  })

  it('keeps browser sessions in session storage and migrates legacy tokens', () => {
    localStorage.setItem('spectarr_access_token', 'legacy-token')
    expect(getAccessToken()).toBe('legacy-token')
    expect(localStorage.getItem('spectarr_access_token')).toBeNull()
    expect(sessionStorage.getItem('spectarr_access_token')).toBe('legacy-token')
  })

  it('revokes the active browser session on logout', async () => {
    setAccessToken('session-token')
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.logout()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/logout', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ Authorization: 'Bearer session-token' })
    }))
  })

  it('creates and normalizes a project', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-1',
      name: 'Proteomics',
      description: 'Mass spec data',
      created_at: '2026-08-25T00:00:00Z',
      updated_at: '2026-08-25T00:00:00Z'
    }), { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.createProject('Proteomics', 'Mass spec data')).resolves.toMatchObject({ id: 'project-1', name: 'Proteomics' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects', expect.objectContaining({ method: 'POST' }))
  })

  it('authenticates artifact downloads', async () => {
    setAccessToken('download-token')
    const fetchMock = vi.fn().mockResolvedValue(new Response('spectrum data', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const artifact = await downloadArtifact('artifact 1')
    expect(artifact.size).toBe(13)
    expect(artifact.type).toBe('text/plain;charset=utf-8')
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/artifacts/artifact%201/download', {
      headers: { Authorization: 'Bearer download-token' }
    })
  })

  it('queues and normalizes every conversion format', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'job-1',
      kind: 'convert',
      state: 'queued',
      progress: 0,
      created_at: '2026-08-25T00:00:00Z'
    }), { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.generateArtifact('run-1', 'mzXML')).resolves.toMatchObject({
      id: 'job-1',
      status: 'queued'
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/runs/run-1/derivatives', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ format: 'mzXML' })
    }))
  })

  it('connects named profiles and scoped processing batches', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => Promise.resolve(new Response(JSON.stringify(
      url.endsWith('/preview')
        ? { scope_type: 'project', run_count: 2, target_count: 2, queue_count: 2 }
        : url.includes('/recipes')
          ? { id: 'profile-1', name: 'Search MGF', output_format: 'MGF', parameters: {}, revision: 1, system: false, enabled: true }
          : { id: 'batch-1', scope_type: 'project', scope_ids: ['project-1'], mode: 'missing', state: 'queued', total_count: 2, queued_count: 2, items: [] },
    ), { status: init?.method === 'POST' ? 202 : 200 })))
    vi.stubGlobal('fetch', fetchMock)
    const parameters = { filters: [], mzPrecision: 64 as const, intensityPrecision: 32 as const, compression: 'zlib' as const, indexed: true }
    await api.createProcessingProfile({ name: 'Search MGF', outputFormat: 'MGF', parameters })
    await api.updateProcessingProfile('profile-1', { parameters })
    await api.previewProcessingBatch({ scopeType: 'project', scopeIds: ['project-1'], recipeIds: ['profile-1'], mode: 'missing' })
    await api.createProcessingBatch({ scopeType: 'project', scopeIds: ['project-1'], recipeIds: ['profile-1'], mode: 'missing' })
    await api.retryProcessingBatch('batch-1')
    await api.cancelProcessingBatch('batch-1')
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/v1/recipes', 'POST'],
      ['/api/v1/recipes/profile-1', 'PATCH'],
      ['/api/v1/processing-batches/preview', 'POST'],
      ['/api/v1/processing-batches', 'POST'],
      ['/api/v1/processing-batches/batch-1/retry', 'POST'],
      ['/api/v1/processing-batches/batch-1/cancel', 'POST']
    ])
  })

  it('connects every dashboard mutation to a concrete API operation', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }))
      if (url.endsWith('/runs/bulk-assignment')) return Promise.resolve(new Response('[]', { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({
        id: 'resource-1',
        name: 'Resource',
        username: 'user',
        display_name: 'User',
        role: 'admin',
        active: true,
        state: 'queued',
        kind: 'convert',
        progress: 0,
        scope: 'global',
        actions: [],
        capabilities: [],
        metadata_json: {},
        status: 'offline',
        url: 'https://example.test/hook',
        event_filters: [],
        scopes: [],
        token: 'one-time-secret'
      }), { status: init?.method === 'POST' ? 201 : 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    await api.createProject('Project')
    await api.changePassword('current-password', 'different-long-password')
    await api.createUser({ username: 'user', displayName: 'User', password: 'long-enough-password', role: 'viewer' })
    await api.updateUser('user-1', { role: 'operator' })
    await api.addProjectMembership('project-1', 'user-1', 'viewer')
    await api.deleteProjectMembership('project-1', 'membership-1')
    await api.createToken('Indexer', ['library:read'])
    await api.deleteToken('token-1')
    await api.registerAgent('Instrument PC')
    await api.updateAgentDestination('agent-1', 'inbox')
    await api.updateAgentEnabled('agent-1', false)
    await api.rotateAgentToken('agent-1')
    await api.createAutomationRule({
      name: 'Extract',
      enabled: true,
      scope: 'global',
      extractMetadata: true,
      profileIds: [],
      deepQc: true
    })
    await api.updateAutomationRule('rule-1', { enabled: false })
    await api.createWebhook({ name: 'Search', url: 'https://example.test/hook', eventFilters: ['artifact.ready'] })
    await api.updateWebhook('webhook-1', false)
    await api.deleteWebhook('webhook-1')
    await api.job('job-1')
    await api.retryJob('job-1')
    await api.extractArtifact('artifact-1', true)
    await api.generateArtifact('run-1', 'mzML')
    await api.assignRuns(['run-1'], 'experiment-1')
    await api.experimentDeletionPreview('experiment-1')
    await api.deleteExperiment('experiment-1', 'Experiment name')
    await api.previewStorageReclaim({ projectId: 'project-1', formats: ['mzML', 'MGF'] })
    await api.reclaimStorage({ projectId: 'project-1', formats: ['mzML', 'MGF'] })

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/v1/projects', 'POST'],
      ['/api/v1/auth/password', 'POST'],
      ['/api/v1/users', 'POST'],
      ['/api/v1/users/user-1', 'PATCH'],
      ['/api/v1/projects/project-1/memberships', 'POST'],
      ['/api/v1/projects/project-1/memberships/membership-1', 'DELETE'],
      ['/api/v1/tokens', 'POST'],
      ['/api/v1/tokens/token-1', 'DELETE'],
      ['/api/v1/agents/register', 'POST'],
      ['/api/v1/agents/agent-1', 'PATCH'],
      ['/api/v1/agents/agent-1', 'PATCH'],
      ['/api/v1/agents/agent-1/rotate-token', 'POST'],
      ['/api/v1/automation-rules', 'POST'],
      ['/api/v1/automation-rules/rule-1', 'PATCH'],
      ['/api/v1/webhooks', 'POST'],
      ['/api/v1/webhooks/webhook-1', 'PATCH'],
      ['/api/v1/webhooks/webhook-1', 'DELETE'],
      ['/api/v1/jobs/job-1', undefined],
      ['/api/v1/jobs/job-1/retry', 'POST'],
      ['/api/v1/artifacts/artifact-1/extract', 'POST'],
      ['/api/v1/runs/run-1/derivatives', 'POST'],
      ['/api/v1/runs/bulk-assignment', 'POST'],
      ['/api/v1/experiments/experiment-1/deletion-preview', undefined],
      ['/api/v1/experiments/experiment-1?confirmation=Experiment%20name', 'DELETE'],
      ['/api/v1/storage/reclaim/preview', 'POST'],
      ['/api/v1/storage/reclaim', 'POST']
    ])
  })

  it('normalizes native artifact format casing on a run', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(
      url.endsWith('/artifacts')
        ? [{ id: 'a1', original_filename: 'result.mgf', format: 'mgf', role: 'derived', byte_size: 10, sha256: 'abc', state: 'ready' }]
        : { id: 'r1', name: 'run', sourceFormat: 'mzxml', created_at: '2026-08-25T00:00:00Z' }
    ), { status: 200 })))
    vi.stubGlobal('fetch', fetchMock)
    const run = await api.run('r1')
    expect(run.sourceFormat).toBe('mzXML')
    expect(run.artifacts[0].format).toBe('MGF')
  })

  it('preserves ordered SDRF columns and connects project metadata actions', async () => {
    const document = {
      id: 'sdrf-1',
      project_id: 'project-1',
      specification_version: 'v1.1.0',
      templates: ['ms-proteomics v1.1.0'],
      columns: ['source name', 'comment[sdrf template]', 'comment[sdrf template]'],
      rows: [{ position: 0, values: ['sample-1', 'ms-proteomics v1.1.0', 'human v1.1.0'] }],
      status: 'draft',
      revision: 2,
      validation_report: {},
      created_at: '2026-08-25T00:00:00Z',
      updated_at: '2026-08-25T00:00:00Z'
    }
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(
      url.endsWith('/validate')
        ? { valid: true, engine: 'sdrf-pipelines', ontology: false, error_count: 0, warning_count: 0, messages: [] }
        : document
    ), { status: 200 })))
    vi.stubGlobal('fetch', fetchMock)

    const normalized = await api.projectSdrf('project-1')
    expect(normalized.columns).toEqual(document.columns)
    await api.saveProjectSdrf('project-1', normalized)
    await api.generateProjectSdrf('project-1')
    await api.validateProjectSdrf('project-1', false)

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/v1/projects/project-1/sdrf', undefined],
      ['/api/v1/projects/project-1/sdrf', 'PUT'],
      ['/api/v1/projects/project-1/sdrf/generate', 'POST'],
      ['/api/v1/projects/project-1/sdrf/validate', 'POST']
    ])
    const savedBody = JSON.parse(String(fetchMock.mock.calls[1][1]?.body))
    expect(savedBody.columns).toEqual(document.columns)
    expect(savedBody.rows[0].values).toEqual(document.rows[0].values)
  })
})
