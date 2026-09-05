import type { ApiToken, Artifact, ArtifactFormat, AuthConfiguration, AutomationRule, ConversionFormat, CurrentUser, Experiment, ExperimentDeletionPreview, ExtractionSummary, Instrument, InstrumentAgent, Job, OverviewData, PaginatedResponse, ProcessingBatch, ProcessingBatchPreview, ProcessingProfile, Project, ProjectMembership, Run, RunStatus, SdrfDocument, SdrfTemplate, SdrfValidationReport, SpectrumCatalogPage, SpectrumQueryRequest, SpxtacularSpectrum, StorageLocation, StorageReclaimPreview, SubmissionPreview, User, UserRole, WebhookDelivery, WebhookDestination } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
const TOKEN_KEY = 'spectarr_access_token'
export const AUTH_EXPIRED_EVENT = 'spectarr-auth-expired'

export const getAccessToken = (): string | null => {
  const sessionToken = sessionStorage.getItem(TOKEN_KEY)
  if (sessionToken) return sessionToken
  const legacyToken = localStorage.getItem(TOKEN_KEY)
  if (!legacyToken) return null
  sessionStorage.setItem(TOKEN_KEY, legacyToken)
  localStorage.removeItem(TOKEN_KEY)
  return legacyToken
}
export const setAccessToken = (value: string): void => {
  sessionStorage.setItem(TOKEN_KEY, value)
  localStorage.removeItem(TOKEN_KEY)
}
export const clearAccessToken = (): void => {
  sessionStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function downloadArtifact(id: string): Promise<Blob> {
  const accessToken = getAccessToken()
  const response = await fetch(`${API_BASE}/artifacts/${encodeURIComponent(id)}/download`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined
  })
  if (!response.ok) {
    if (response.status === 401 && accessToken) {
      clearAccessToken()
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    }
    throw new ApiError(response.status, `Download failed with status ${response.status}`)
  }
  return response.blob()
}

async function download(path: string): Promise<Blob> {
  const accessToken = getAccessToken()
  const response = await fetch(`${API_BASE}${path}`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined
  })
  if (!response.ok) {
    throw new ApiError(response.status, `Download failed with status ${response.status}`)
  }
  return response.blob()
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = typeof FormData !== 'undefined' && init?.body instanceof FormData
  const accessToken = getAccessToken()
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers
    }
  })

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`
    try {
      const body = await response.json() as { detail?: string }
      message = body.detail ?? message
    } catch {
      // Keep the status based message when the body is not JSON
    }
    if (response.status === 401 && accessToken) {
      clearAccessToken()
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    }
    throw new ApiError(response.status, message)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export interface RunListQuery {
  projectId?: string
  experimentId?: string
  query?: string
  assignmentStatus?: string
  offset?: number
  limit?: number
}

export interface RunPage {
  items: Run[]
  total: number
  nextOffset: number | null
  experimentCounts: Record<string, number>
}

async function allPages<T>(path: string): Promise<T[]> {
  const [pathname, search = ''] = path.split('?')
  const parameters = new URLSearchParams(search)
  parameters.set('limit', '100')
  const items: T[] = []
  let offset = 0
  while (true) {
    parameters.set('offset', String(offset))
    const page = normalizeList(await request<T[] | PaginatedResponse<T>>(`${pathname}?${parameters}`))
    items.push(...page)
    if (page.length < 100) return items
    offset += page.length
  }
}

async function runPage(values: RunListQuery = {}): Promise<RunPage> {
  const parameters = new URLSearchParams({ page: 'true', offset: String(values.offset ?? 0), limit: String(values.limit ?? 50) })
  if (values.projectId) parameters.set('project_id', values.projectId)
  if (values.experimentId) parameters.set('experiment_id', values.experimentId)
  if (values.query) parameters.set('query', values.query)
  if (values.assignmentStatus) parameters.set('assignment_status', values.assignmentStatus)
  const page = await request<{ items: unknown[], total: number, next_offset: number | null, experiment_counts: Record<string, number> }>(`/runs?${parameters}`)
  return { items: page.items.map(item => normalizeRun(item)), total: page.total, nextOffset: page.next_offset, experimentCounts: page.experiment_counts }
}

async function allRuns(values: RunListQuery = {}): Promise<Run[]> {
  const items: Run[] = []
  let offset: number | null = 0
  while (offset !== null) {
    const page = await runPage({ ...values, offset, limit: 500 })
    items.push(...page.items)
    offset = page.nextOffset
  }
  return items
}

const normalizeList = <T>(value: T[] | PaginatedResponse<T>): T[] => Array.isArray(value) ? value : value.items

type ApiRecord = Record<string, unknown>

const textValue = (value: unknown, fallback = ''): string => typeof value === 'string' ? value : fallback
const numberValue = (value: unknown, fallback = 0): number => typeof value === 'number' ? value : fallback
const recordValue = (value: unknown): ApiRecord => value !== null && typeof value === 'object' ? value as ApiRecord : {}
const stringList = (value: unknown): string[] => Array.isArray(value) ? value.filter(item => typeof item === 'string') : []
const numberPair = (value: unknown): [number, number] | undefined => Array.isArray(value) && value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number'
  ? [value[0], value[1]]
  : undefined

function normalizeExtraction(value: unknown): ExtractionSummary | undefined {
  const item = recordValue(value)
  if (!item.id && !item.status && !item.summary_json && !item.summary) return undefined
  const payload = recordValue(item.payload)
  const summary = recordValue(item.summary ?? item.summary_json ?? payload.qc_summary)
  const metrics = recordValue(item.metrics ?? item.metrics_json)
  const chromatograms = recordValue(metrics.chromatograms ?? summary.chromatograms ?? summary.chromatogram_preview)
  const points = (value: unknown) => Array.isArray(value) ? value.flatMap(entry => {
    const point = recordValue(entry)
    const timeValue = point.time ?? point.rt ?? point.retention_time_seconds
    const time = typeof timeValue === 'number' && point.retention_time_seconds !== undefined ? timeValue / 60 : timeValue
    const intensity = point.intensity ?? point.value
    return typeof time === 'number' && typeof intensity === 'number' ? [{ time, intensity }] : []
  }) : []
  const spectraByMsLevel = recordValue(summary.spectra_by_ms_level ?? summary.ms_level_counts)
  const precursors = recordValue(summary.precursors)
  const chargeCounts = recordValue(summary.charge_counts ?? precursors.charge_counts)
  const retentionTimes = recordValue(summary.retention_time_seconds)
  const mzRange = recordValue(summary.mz_range)
  const collisionEnergy = recordValue(summary.collision_energy ?? precursors.collision_energy)
  const peakCount = recordValue(summary.peak_count)
  const ionMobility = recordValue(summary.ion_mobility)
  const dia = recordValue(summary.dia)
  const durationSeconds = numberValue(summary.acquisition_duration_seconds)
  const representations = stringList(summary.representations)
  return {
    id: textValue(item.id, 'latest'),
    status: textValue(item.status, 'succeeded') as ExtractionSummary['status'],
    extractor: textValue(item.extractor ?? item.extractor_name, 'unknown'),
    extractorVersion: textValue(item.extractor_version, 'unknown'),
    schemaVersion: textValue(item.schema_version, '1'),
    sourceSha256: textValue(item.source_sha256),
    startedAt: textValue(item.started_at) || undefined,
    finishedAt: textValue(item.finished_at ?? item.created_at) || undefined,
    error: textValue(item.error) || undefined,
    warnings: stringList(item.warnings ?? summary.warnings),
    spectrumCount: numberValue(summary.spectrum_count ?? summary.spectra_count) || undefined,
    spectraByMsLevel: Object.fromEntries(Object.entries(spectraByMsLevel).map(([key, count]) => [key, numberValue(count)])),
    ms2Count: numberValue(summary.ms2_count ?? spectraByMsLevel['2']) || undefined,
    durationMinutes: numberValue(summary.duration_minutes) || (durationSeconds ? durationSeconds / 60 : undefined),
    rtRange: numberPair(summary.rt_range) ?? (typeof retentionTimes.min === 'number' && typeof retentionTimes.max === 'number' ? [retentionTimes.min / 60, retentionTimes.max / 60] : undefined),
    mzRange: numberPair(summary.mz_range) ?? (typeof mzRange.min === 'number' && typeof mzRange.max === 'number' ? [mzRange.min, mzRange.max] : undefined),
    polarities: stringList(summary.polarities),
    representation: textValue(summary.representation, representations.join(', ')) || undefined,
    precursorCount: numberValue(summary.precursor_count ?? precursors.count) || undefined,
    chargeCounts: Object.fromEntries(Object.entries(chargeCounts).map(([key, count]) => [key, numberValue(count)])),
    collisionEnergyRange: numberPair(summary.collision_energy_range) ?? (typeof collisionEnergy.min === 'number' && typeof collisionEnergy.max === 'number' ? [collisionEnergy.min, collisionEnergy.max] : undefined),
    peakCountMean: numberValue(summary.peak_count_mean ?? peakCount.mean) || undefined,
    ionMobility: typeof summary.ion_mobility === 'boolean' ? summary.ion_mobility : typeof ionMobility.present === 'boolean' ? ionMobility.present : undefined,
    diaWindowCount: numberValue(summary.dia_window_count ?? dia.window_count) || undefined,
    tic: points(chromatograms.tic ?? metrics.tic ?? summary.tic),
    bpc: points(chromatograms.bpc ?? metrics.bpc ?? summary.bpc),
    raw: { ...recordValue(payload.metadata), ...summary, ...metrics }
  }
}

function normalizeArtifact(value: unknown): Artifact {
  const item = recordValue(value)
  const state = textValue(item.status ?? item.state, 'verified')
  return {
    id: textValue(item.id),
    name: textValue(item.name ?? item.original_filename, 'Unnamed artifact'),
    format: normalizeFormat(item.format),
    role: textValue(item.role, 'source') as Artifact['role'],
    sizeBytes: numberValue(item.sizeBytes ?? item.byte_size),
    checksum: textValue(item.checksum, item.sha256 ? `sha256:${item.sha256}` : 'Checksum pending'),
    status: state === 'ready' ? 'verified' : state === 'missing' ? 'purged' : state as Artifact['status'],
    libraryPath: textValue(item.libraryPath ?? item.library_path) || undefined,
    materializationMode: (textValue(item.materializationMode ?? item.materialization_mode) || undefined) as Artifact['materializationMode']
  }
}

function normalizeRun(value: unknown, artifacts?: unknown[]): Run {
  const item = recordValue(value)
  const metadata = recordValue(item.metadata_json)
  const extraction = normalizeExtraction(item.latest_extraction ?? item.extraction)
  const sourceClass = textValue(item.source_class)
  const sourceFormat = textValue(item.sourceFormat, sourceClass === 'open' ? 'mzML' : sourceClass === 'spectrum_list' ? 'MGF' : 'RAW')
  const normalizedArtifacts = (artifacts ?? (Array.isArray(item.artifacts) ? item.artifacts : [])).map(normalizeArtifact)
  return {
    id: textValue(item.id),
    projectId: textValue(item.projectId ?? item.project_id) || undefined,
    experimentId: textValue(item.experimentId ?? item.experiment_id) || undefined,
    name: textValue(item.name, 'Unnamed run'),
    projectName: textValue(item.projectName, textValue(metadata.project_name, 'Unassigned')),
    experimentName: textValue(item.experimentName, textValue(metadata.experiment_name, 'Unassigned')),
    sampleName: textValue(item.sampleName, textValue(metadata.sample_name, 'Unassigned')),
    instrument: textValue(item.instrument, textValue(metadata.instrument, 'Unknown')),
    acquiredAt: textValue(item.acquiredAt ?? item.acquired_at ?? item.created_at, new Date().toISOString()),
    importedAt: textValue(item.importedAt ?? item.created_at, new Date().toISOString()),
    status: textValue(item.status, normalizedArtifacts.length ? 'ready' : 'warning') as RunStatus,
    sourceFormat: normalizeFormat(sourceFormat),
    sizeBytes: numberValue(item.sizeBytes, normalizedArtifacts.reduce((sum, artifact) => sum + artifact.sizeBytes, 0)),
    spectraCount: numberValue(item.spectraCount ?? extraction?.spectrumCount ?? metadata.spectra_count) || undefined,
    ms2Count: numberValue(item.ms2Count ?? extraction?.ms2Count ?? metadata.ms2_count) || undefined,
    durationMinutes: numberValue(item.durationMinutes ?? extraction?.durationMinutes ?? metadata.duration_minutes) || undefined,
    extraction,
    metadata,
    artifacts: normalizedArtifacts,
    assignmentStatus: textValue(item.assignmentStatus ?? item.assignment_status, 'assigned') as Run['assignmentStatus']
  }
}

function normalizeFormat(value: unknown): ArtifactFormat {
  const format = textValue(value, 'RAW')
  return ({
    RAW: 'RAW',
    MZML: 'mzML',
    MZXML: 'mzXML',
    MGF: 'MGF',
    MS2: 'MS2',
    MSP: 'MSP',
    VENDOR_DIRECTORY: 'RAW',
    PARQUET: 'Parquet'
  } as const)[format.toUpperCase() as 'RAW' | 'MZML' | 'MZXML' | 'MGF' | 'MS2' | 'MSP' | 'VENDOR_DIRECTORY' | 'PARQUET'] ?? format as ArtifactFormat
}

function normalizeProject(value: unknown): Project {
  const item = recordValue(value)
  const sdrf = recordValue(item.sdrf)
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed project'),
    description: textValue(item.description) || undefined,
    runCount: numberValue(item.runCount ?? item.run_count),
    sizeBytes: numberValue(item.sizeBytes ?? item.size_bytes),
    updatedAt: textValue(item.updatedAt ?? item.updated_at ?? item.created_at, new Date().toISOString()),
    systemKey: textValue(item.system_key ?? item.systemKey) || undefined,
    metadata: recordValue(item.metadata_json ?? item.metadata),
    sdrf: sdrf.status ? {
      status: textValue(sdrf.status) as NonNullable<Project['sdrf']>['status'],
      revision: numberValue(sdrf.revision),
      rowCount: numberValue(sdrf.row_count ?? sdrf.rowCount),
      sourceFilename: textValue(sdrf.source_filename ?? sdrf.sourceFilename) || undefined
    } : undefined
  }
}

function normalizeValidation(value: unknown): SdrfValidationReport {
  const item = recordValue(value)
  return {
    valid: Boolean(item.valid),
    engine: textValue(item.engine, 'spectarr-structural'),
    ontology: Boolean(item.ontology),
    errorCount: numberValue(item.error_count ?? item.errorCount),
    warningCount: numberValue(item.warning_count ?? item.warningCount),
    messages: (Array.isArray(item.messages) ? item.messages : []).map(value => {
      const message = recordValue(value)
      return {
        severity: textValue(message.severity, 'info') as 'error' | 'warning' | 'info',
        code: textValue(message.code),
        message: textValue(message.message),
        row: typeof message.row === 'number' ? message.row : undefined,
        column: typeof message.column === 'number' ? message.column : undefined
      }
    })
  }
}

function normalizeSdrf(value: unknown): SdrfDocument {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    projectId: textValue(item.project_id),
    specificationVersion: textValue(item.specification_version, 'v1.1.0'),
    templates: stringList(item.templates),
    columns: stringList(item.columns),
    rows: (Array.isArray(item.rows) ? item.rows : []).map(value => {
      const row = recordValue(value)
      return {
        id: textValue(row.id) || undefined,
        position: numberValue(row.position),
        values: stringList(row.values),
        sampleId: textValue(row.sample_id) || undefined,
        runId: textValue(row.run_id) || undefined,
        artifactId: textValue(row.artifact_id) || undefined
      }
    }),
    status: textValue(item.status, 'draft') as SdrfDocument['status'],
    revision: numberValue(item.revision, 1),
    sourceFilename: textValue(item.source_filename) || undefined,
    contentSha256: textValue(item.content_sha256) || undefined,
    validationEngine: textValue(item.validation_engine) || undefined,
    validationReport: Object.keys(recordValue(item.validation_report)).length ? normalizeValidation(item.validation_report) : undefined,
    createdAt: textValue(item.created_at, new Date().toISOString()),
    updatedAt: textValue(item.updated_at, new Date().toISOString())
  }
}

function normalizeSubmission(value: unknown): SubmissionPreview {
  const item = recordValue(value)
  return {
    projectId: textValue(item.project_id),
    sourceCount: numberValue(item.source_count),
    derivativeCount: numberValue(item.derivative_count),
    totalBytes: numberValue(item.total_bytes),
    sdrfStatus: textValue(item.sdrf_status),
    sdrfRevision: numberValue(item.sdrf_revision) || undefined,
    mappedRows: numberValue(item.mapped_rows),
    unmappedRows: numberValue(item.unmapped_rows),
    ready: Boolean(item.ready)
  }
}

function normalizeJob(value: unknown): Job {
  const item = recordValue(value)
  const rawState = textValue(item.status ?? item.state, 'queued')
  const status = rawState === 'succeeded' ? 'complete' : rawState === 'cancelled' ? 'failed' : rawState
  const progress = numberValue(item.progress)
  return {
    id: textValue(item.id),
    kind: textValue(item.kind, 'import') as Job['kind'],
    runName: textValue(item.runName ?? item.run_name, 'System'),
    status: status as Job['status'],
    progress: progress <= 1 ? Math.round(progress * 100) : Math.round(progress),
    detail: textValue(item.detail ?? item.error, textValue(item.kind, 'Job')),
    createdAt: textValue(item.createdAt ?? item.created_at, new Date().toISOString())
  }
}

function normalizeStorage(value: unknown): StorageLocation {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    name: textValue(item.name),
    path: textValue(item.path),
    kind: textValue(item.kind, 'filesystem') as StorageLocation['kind'],
    usedBytes: numberValue(item.usedBytes ?? item.used_bytes),
    capacityBytes: numberValue(item.capacityBytes ?? item.capacity_bytes),
    status: textValue(item.status, 'healthy') as StorageLocation['status'],
    artifactCount: numberValue(item.artifactCount ?? item.artifact_count)
  }
}

function normalizeStorageReclaim(value: unknown): StorageReclaimPreview {
  const item = recordValue(value)
  const counts = recordValue(item.format_counts)
  return {
    artifactCount: numberValue(item.artifact_count),
    reclaimableBytes: numberValue(item.reclaimable_bytes),
    formatCounts: Object.fromEntries(Object.entries(counts).map(([key, count]) => [key, numberValue(count)])),
    blockedCount: numberValue(item.blocked_count)
  }
}

function normalizeUser(value: unknown): User {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    username: textValue(item.username),
    displayName: textValue(item.display_name ?? item.displayName, textValue(item.username)),
    role: textValue(item.role, 'viewer') as UserRole,
    active: typeof item.active === 'boolean' ? item.active : true,
    createdAt: textValue(item.created_at ?? item.createdAt, new Date().toISOString()),
    lastLoginAt: textValue(item.last_login_at ?? item.lastLoginAt) || undefined
  }
}

function normalizeToken(value: unknown): ApiToken {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    name: textValue(item.name),
    scopes: stringList(item.scopes),
    createdAt: textValue(item.created_at ?? item.createdAt, new Date().toISOString()),
    expiresAt: textValue(item.expires_at ?? item.expiresAt) || undefined,
    lastUsedAt: textValue(item.last_used_at ?? item.lastUsedAt) || undefined,
    token: textValue(item.token ?? item.secret) || undefined
  }
}

function normalizeMembership(value: unknown): ProjectMembership {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    projectId: textValue(item.project_id ?? item.projectId),
    userId: textValue(item.user_id ?? item.userId),
    role: textValue(item.role, 'viewer') as UserRole,
    createdAt: textValue(item.created_at ?? item.createdAt, new Date().toISOString())
  }
}

function normalizeAgent(value: unknown): InstrumentAgent {
  const item = recordValue(value)
  const metadata = recordValue(item.metadata_json)
  const capacity = recordValue(item.capacity)
  const queue = recordValue(capacity.queue)
  const queued = Object.values(queue).reduce<number>((total, value) => total + numberValue(value), 0)
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed agent'),
    enabled: typeof item.enabled === 'boolean' ? item.enabled : true,
    status: (item.enabled === false ? 'disabled' : textValue(item.status, 'offline')) as InstrumentAgent['status'],
    platform: textValue(item.platform, textValue(metadata.platform, 'Unknown')),
    version: textValue(item.version, textValue(metadata.version, 'Unknown')),
    watchPaths: stringList(item.watch_paths ?? metadata.watch_paths ?? capacity.watch_paths),
    backlog: numberValue(item.backlog ?? metadata.backlog ?? capacity.queue_depth ?? capacity.backlog, queued),
    lastSeenAt: textValue(item.last_seen_at ?? item.lastSeenAt) || undefined,
    lastError: textValue(item.last_error ?? metadata.last_error) || undefined,
    createdAt: textValue(item.created_at ?? item.createdAt, new Date().toISOString()),
    destinationMode: textValue(item.destination_mode ?? item.destinationMode, 'inbox') as InstrumentAgent['destinationMode'],
    destinationExperimentId: textValue(item.destination_experiment_id ?? item.destinationExperimentId) || undefined
  }
}

function normalizeExperiment(value: unknown): Experiment {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    projectId: textValue(item.project_id ?? item.projectId),
    name: textValue(item.name, 'Unnamed experiment'),
    description: textValue(item.description) || undefined,
    intakeAgentId: textValue(item.intake_agent_id ?? item.intakeAgentId) || undefined
  }
}

function normalizeInstrument(value: unknown): Instrument {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    name: textValue(item.name),
    vendor: textValue(item.vendor) || undefined,
    model: textValue(item.model) || undefined,
    serialNumber: textValue(item.serial_number ?? item.serialNumber) || undefined,
    enabled: typeof item.enabled === 'boolean' ? item.enabled : true
  }
}

function normalizeRule(value: unknown): AutomationRule {
  const item = recordValue(value)
  const actions = Array.isArray(item.actions) ? item.actions.map(recordValue) : []
  const hasAction = (kind: string) => actions.some(action => textValue(action.kind ?? action.type) === kind)
  const extractionAction = actions.find(action => textValue(action.kind ?? action.type) === 'extract_metadata')
  const extractionParameters = recordValue(extractionAction?.parameters)
  const profileIds = actions
    .filter(action => textValue(action.kind ?? action.type) === 'convert')
    .map(action => textValue(action.recipe_id ?? action.recipeId))
    .filter(Boolean)
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed rule'),
    enabled: typeof item.enabled === 'boolean' ? item.enabled : true,
    scope: textValue(item.scope, 'global') as AutomationRule['scope'],
    projectId: textValue(item.project_id) || undefined,
    instrumentId: textValue(item.instrument_id) || undefined,
    generateMzml: hasAction('convert'),
    profileIds,
    extractMetadata: hasAction('extract_metadata'),
    deepQc: Boolean(extractionAction) && extractionParameters.deep_qc !== false,
    createdAt: textValue(item.created_at, new Date().toISOString())
  }
}

function normalizeProfile(value: unknown): ProcessingProfile {
  const item = recordValue(value)
  const parameters = recordValue(item.parameters)
  return {
    id: textValue(item.id),
    name: textValue(item.name, 'Unnamed profile'),
    description: textValue(item.description) || undefined,
    outputFormat: normalizeFormat(item.output_format ?? item.outputFormat) as ConversionFormat,
    revision: numberValue(item.revision, 1),
    system: Boolean(item.system),
    enabled: typeof item.enabled === 'boolean' ? item.enabled : true,
    parameters: {
      preset: (textValue(parameters.preset) || undefined) as ProcessingProfile['parameters']['preset'],
      filters: Array.isArray(parameters.filters) ? parameters.filters.map(recordValue) : [],
      mzPrecision: numberValue(parameters.mz_precision, 64) as 32 | 64,
      intensityPrecision: numberValue(parameters.intensity_precision, 32) as 32 | 64,
      compression: textValue(parameters.compression, 'zlib') as ProcessingProfile['parameters']['compression'],
      indexed: parameters.indexed !== false
    },
    createdAt: textValue(item.created_at, new Date().toISOString()),
    updatedAt: textValue(item.updated_at, new Date().toISOString())
  }
}

function normalizeBatchPreview(value: unknown): ProcessingBatchPreview {
  const item = recordValue(value)
  return {
    scopeType: textValue(item.scope_type) as ProcessingBatchPreview['scopeType'],
    runCount: numberValue(item.run_count),
    targetCount: numberValue(item.target_count),
    queueCount: numberValue(item.queue_count),
    currentCount: numberValue(item.current_count),
    staleCount: numberValue(item.stale_count),
    incompatibleCount: numberValue(item.incompatible_count),
    queuedCount: numberValue(item.queued_count)
  }
}

function normalizeBatch(value: unknown): ProcessingBatch {
  const item = recordValue(value)
  const rawItems = Array.isArray(item.items) ? item.items : []
  return {
    id: textValue(item.id),
    scopeType: textValue(item.scope_type) as ProcessingBatch['scopeType'],
    scopeIds: stringList(item.scope_ids),
    mode: textValue(item.mode) as ProcessingBatch['mode'],
    label: textValue(item.label) || undefined,
    requestedBy: textValue(item.requested_by) || undefined,
    state: textValue(item.state) as ProcessingBatch['state'],
    totalCount: numberValue(item.total_count),
    queuedCount: numberValue(item.queued_count),
    runningCount: numberValue(item.running_count),
    succeededCount: numberValue(item.succeeded_count),
    failedCount: numberValue(item.failed_count),
    skippedCount: numberValue(item.skipped_count),
    cancelledCount: numberValue(item.cancelled_count),
    progress: Math.round(numberValue(item.progress) * 100),
    createdAt: textValue(item.created_at, new Date().toISOString()),
    updatedAt: textValue(item.updated_at, new Date().toISOString()),
    items: rawItems.map(value => {
      const batchItem = recordValue(value)
      return {
        id: textValue(batchItem.id),
        runId: textValue(batchItem.run_id),
        runName: textValue(batchItem.run_name),
        recipeId: textValue(batchItem.recipe_id),
        recipeName: textValue(batchItem.recipe_name),
        outputFormat: normalizeFormat(batchItem.output_format) as ConversionFormat,
        jobId: textValue(batchItem.job_id) || undefined,
        disposition: textValue(batchItem.disposition),
        reason: textValue(batchItem.reason) || undefined,
        state: textValue(batchItem.state) as ProcessingBatch['items'][number]['state'],
        progress: Math.round(numberValue(batchItem.progress) * 100),
        error: textValue(batchItem.error) || undefined
      }
    })
  }
}

function normalizeWebhook(value: unknown): WebhookDestination {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    name: textValue(item.name),
    url: textValue(item.url),
    eventFilters: stringList(item.event_filters ?? item.eventFilters),
    enabled: typeof item.enabled === 'boolean' ? item.enabled : true,
    createdAt: textValue(item.created_at ?? item.createdAt, new Date().toISOString()),
    signingSecret: textValue(item.signing_secret) || undefined
  }
}

function normalizeWebhookDelivery(value: unknown): WebhookDelivery {
  const item = recordValue(value)
  return {
    id: textValue(item.id),
    destinationId: textValue(item.destination_id),
    status: textValue(item.status),
    attempts: numberValue(item.attempts),
    responseStatus: numberValue(item.response_status) || undefined,
    lastError: textValue(item.last_error) || undefined,
    createdAt: textValue(item.created_at, new Date().toISOString())
  }
}

export const api = {
  systemHealth: async () => {
    const response = await request<ApiRecord>('/system/health')
    return {
      status: textValue(response.status, 'unknown'),
      database: textValue(response.database, 'unknown'),
      storage: textValue(response.storage, 'unknown'),
      version: textValue(response.version, 'unknown')
    }
  },
  authConfiguration: async (): Promise<AuthConfiguration> => {
    const response = await request<ApiRecord>('/auth/config')
    return {
      mode: response.mode === 'local' ? 'local' : 'password',
      localUser: textValue(response.local_user) || undefined,
      allowRemoteNoAuth: Boolean(response.allow_remote_no_auth)
    }
  },
  bootstrapStatus: async () => {
    const response = await request<ApiRecord>('/auth/bootstrap/status')
    return Boolean(response.required)
  },
  bootstrap: async (username: string, password: string) => {
    const response = await request<ApiRecord>('/auth/bootstrap', {
      method: 'POST',
      body: JSON.stringify({ username, password })
    })
    return {
      accessToken: textValue(response.access_token),
      user: normalizeUser(response.user) satisfies CurrentUser
    }
  },
  login: async (username: string, password: string) => {
    const response = await request<ApiRecord>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password })
    })
    return {
      accessToken: textValue(response.access_token),
      user: normalizeUser(response.user) satisfies CurrentUser
    }
  },
  logout: async () => request<void>('/auth/logout', { method: 'POST' }),
  changePassword: async (currentPassword: string, newPassword: string) => request<void>('/auth/password', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
  }),
  me: async () => normalizeUser(await request('/auth/me')),
  users: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/users')).map(normalizeUser),
  createUser: async (values: { username: string, displayName: string, password: string, role: UserRole }) => normalizeUser(await request('/users', {
    method: 'POST',
    body: JSON.stringify({ username: values.username, display_name: values.displayName, password: values.password, role: values.role })
  })),
  updateUser: async (id: string, values: Partial<{ active: boolean, role: UserRole, displayName: string }>) => normalizeUser(await request(`/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ active: values.active, role: values.role, display_name: values.displayName })
  })),
  projectMemberships: async (projectId: string) => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>(`/projects/${encodeURIComponent(projectId)}/memberships`)).map(normalizeMembership),
  addProjectMembership: async (projectId: string, userId: string, role: UserRole) => normalizeMembership(await request(`/projects/${encodeURIComponent(projectId)}/memberships`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, role })
  })),
  deleteProjectMembership: async (projectId: string, membershipId: string) => request<void>(`/projects/${encodeURIComponent(projectId)}/memberships/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  tokens: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/tokens')).map(normalizeToken),
  createToken: async (name: string, scopes: string[]) => normalizeToken(await request('/tokens', {
    method: 'POST',
    body: JSON.stringify({ name, scopes })
  })),
  deleteToken: async (id: string) => request<void>(`/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  agents: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/agents')).map(normalizeAgent),
  instruments: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/instruments')).map(normalizeInstrument),
  registerAgent: async (name: string, destinationExperimentId?: string) => {
    const response = await request<ApiRecord>('/agents/register', {
      method: 'POST',
      body: JSON.stringify({ name, capabilities: ['resumable_upload', 'bundle_upload', 'offline_queue', 'polling'], metadata_json: {}, destination_experiment_id: destinationExperimentId })
    })
    return { agent: normalizeAgent(response.agent ?? response), token: textValue(response.token) }
  },
  updateAgentDestination: async (id: string, mode: 'inbox' | 'direct', destinationExperimentId?: string) => normalizeAgent(await request(`/agents/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ destination_mode: mode, destination_experiment_id: destinationExperimentId })
  })),
  updateAgentEnabled: async (id: string, enabled: boolean) => normalizeAgent(await request(`/agents/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled })
  })),
  rotateAgentToken: async (id: string) => {
    const response = await request<ApiRecord>(`/agents/${encodeURIComponent(id)}/rotate-token`, { method: 'POST' })
    return { agent: normalizeAgent(response), token: textValue(response.token) }
  },
  automationRules: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/automation-rules')).map(normalizeRule),
  createAutomationRule: async (values: Omit<AutomationRule, 'id' | 'createdAt' | 'generateMzml'>) => normalizeRule(await request('/automation-rules', {
    method: 'POST',
    body: JSON.stringify({
      name: values.name,
      enabled: values.enabled,
      scope: values.scope,
      project_id: values.projectId,
      instrument_id: values.instrumentId,
      actions: [
        ...(values.extractMetadata ? [{ kind: 'extract_metadata', parameters: { deep_qc: values.deepQc } }] : []),
        ...values.profileIds.map(recipeId => ({ kind: 'convert', recipe_id: recipeId })),
      ]
    })
  })),
  updateAutomationRule: async (id: string, values: Partial<{ enabled: boolean, name: string }>) => normalizeRule(await request(`/automation-rules/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(values)
  })),
  processingProfiles: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/recipes')).map(normalizeProfile).filter(profile => profile.system || !profile.name.startsWith('default-')),
  createProcessingProfile: async (values: {
    name: string
    description?: string
    outputFormat: ConversionFormat
    parameters: ProcessingProfile['parameters']
  }) => normalizeProfile(await request('/recipes', {
    method: 'POST',
    body: JSON.stringify({
      name: values.name,
      description: values.description,
      output_format: values.outputFormat,
      parameters: {
        preset: values.parameters.preset,
        filters: values.parameters.filters,
        mz_precision: values.parameters.mzPrecision,
        intensity_precision: values.parameters.intensityPrecision,
        compression: values.parameters.compression,
        indexed: values.parameters.indexed
      }
    })
  })),
  updateProcessingProfile: async (id: string, values: Partial<{
    name: string
    description: string
    outputFormat: ConversionFormat
    parameters: ProcessingProfile['parameters']
    enabled: boolean
  }>) => normalizeProfile(await request(`/recipes/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({
      name: values.name,
      description: values.description,
      output_format: values.outputFormat,
      enabled: values.enabled,
      parameters: values.parameters ? {
        preset: values.parameters.preset,
        filters: values.parameters.filters,
        mz_precision: values.parameters.mzPrecision,
        intensity_precision: values.parameters.intensityPrecision,
        compression: values.parameters.compression,
        indexed: values.parameters.indexed
      } : undefined
    })
  })),
  previewProcessingBatch: async (values: {
    scopeType: ProcessingBatchPreview['scopeType']
    scopeIds: string[]
    recipeIds: string[]
    mode: ProcessingBatch['mode']
  }) => normalizeBatchPreview(await request('/processing-batches/preview', {
    method: 'POST',
    body: JSON.stringify({ scope_type: values.scopeType, scope_ids: values.scopeIds, recipe_ids: values.recipeIds, mode: values.mode })
  })),
  createProcessingBatch: async (values: {
    scopeType: ProcessingBatchPreview['scopeType']
    scopeIds: string[]
    recipeIds: string[]
    mode: ProcessingBatch['mode']
    label?: string
  }) => normalizeBatch(await request('/processing-batches', {
    method: 'POST',
    body: JSON.stringify({ scope_type: values.scopeType, scope_ids: values.scopeIds, recipe_ids: values.recipeIds, mode: values.mode, label: values.label })
  })),
  processingBatches: async () => (await allPages<unknown>('/processing-batches')).map(normalizeBatch),
  processingBatch: async (id: string) => normalizeBatch(await request(`/processing-batches/${encodeURIComponent(id)}`)),
  retryProcessingBatch: async (id: string) => normalizeBatch(await request(`/processing-batches/${encodeURIComponent(id)}/retry`, { method: 'POST' })),
  cancelProcessingBatch: async (id: string) => normalizeBatch(await request(`/processing-batches/${encodeURIComponent(id)}/cancel`, { method: 'POST' })),
  webhooks: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/webhooks')).map(normalizeWebhook),
  createWebhook: async (values: { name: string, url: string, eventFilters: string[] }) => normalizeWebhook(await request('/webhooks', {
    method: 'POST',
    body: JSON.stringify({ name: values.name, url: values.url, event_filters: values.eventFilters, enabled: true })
  })),
  updateWebhook: async (id: string, enabled: boolean) => normalizeWebhook(await request(`/webhooks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled })
  })),
  deleteWebhook: async (id: string) => request<void>(`/webhooks/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  webhookDeliveries: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/webhook-deliveries')).map(normalizeWebhookDelivery),
  overview: async () => {
    const raw = await request<ApiRecord>('/overview')
    const health = recordValue(raw.health)
    const stats = recordValue(raw.stats)
    const rawFormatCounts = recordValue(stats.formatCounts ?? stats.format_counts)
    const formatCounts = Object.fromEntries(
      Object.entries(rawFormatCounts).map(([key, value]) => [key, numberValue(value)])
    )
    return {
      runs: (Array.isArray(raw.runs) ? raw.runs : []).map(item => normalizeRun(item)),
      jobs: (Array.isArray(raw.jobs) ? raw.jobs : []).map(normalizeJob),
      projects: (Array.isArray(raw.projects) ? raw.projects : []).map(normalizeProject),
      storage: (Array.isArray(raw.storage) ? raw.storage : []).map(normalizeStorage),
      health: {
        api: textValue(health.api, 'online') as 'online' | 'offline',
        workers: numberValue(health.workers),
        queueDepth: numberValue(health.queueDepth ?? health.queue_depth),
        version: textValue(health.version, 'unknown')
      },
      stats: {
        runs: numberValue(stats.runs),
        artifacts: numberValue(stats.artifacts),
        formatCounts
      }
    } satisfies OverviewData
  },
  runs: allRuns,
  runPage,
  inbox: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/inbox')).map(item => normalizeRun(item)),
  run: async (id: string) => {
    const encoded = encodeURIComponent(id)
    const [run, artifacts] = await Promise.all([
      request<ApiRecord>(`/runs/${encoded}`),
      request<unknown[]>(`/runs/${encoded}/artifacts`)
    ])
    return normalizeRun(run, artifacts.length ? artifacts : undefined)
  },
  projects: async () => (await allPages<unknown>('/projects')).map(normalizeProject),
  project: async (id: string) => normalizeProject(await request(`/projects/${encodeURIComponent(id)}`)),
  updateProject: async (id: string, values: { name?: string, description?: string, metadata?: Record<string, unknown> }) => normalizeProject(await request(`/projects/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name: values.name, description: values.description, metadata_json: values.metadata })
  })),
  sdrfTemplates: async (): Promise<SdrfTemplate[]> => {
    const value = recordValue(await request('/sdrf/templates'))
    return (Array.isArray(value.templates) ? value.templates : []).map(value => {
      const template = recordValue(value)
      return { name: textValue(template.name), version: textValue(template.version), kind: textValue(template.kind) }
    })
  },
  projectSdrf: async (projectId: string) => normalizeSdrf(await request(`/projects/${encodeURIComponent(projectId)}/sdrf`)),
  generateProjectSdrf: async (projectId: string) => normalizeSdrf(await request(`/projects/${encodeURIComponent(projectId)}/sdrf/generate`, { method: 'POST' })),
  importProjectSdrf: async (projectId: string, file: File, synchronize = true) => {
    const body = new FormData()
    body.set('file', file)
    body.set('synchronize', String(synchronize))
    return normalizeSdrf(await request(`/projects/${encodeURIComponent(projectId)}/sdrf/import`, { method: 'POST', body }))
  },
  saveProjectSdrf: async (projectId: string, document: Pick<SdrfDocument, 'columns' | 'rows' | 'templates' | 'sourceFilename'>, synchronize = true) => normalizeSdrf(await request(`/projects/${encodeURIComponent(projectId)}/sdrf`, {
    method: 'PUT',
    body: JSON.stringify({
      columns: document.columns,
      rows: document.rows.map(row => ({ values: row.values })),
      templates: document.templates,
      source_filename: document.sourceFilename,
      synchronize
    })
  })),
  validateProjectSdrf: async (projectId: string, ontology: boolean) => normalizeValidation(await request(`/projects/${encodeURIComponent(projectId)}/sdrf/validate`, {
    method: 'POST',
    body: JSON.stringify({ ontology })
  })),
  submissionPreview: async (projectId: string) => normalizeSubmission(await request(`/projects/${encodeURIComponent(projectId)}/submission/preview`)),
  downloadProjectSdrf: async (projectId: string) => download(`/projects/${encodeURIComponent(projectId)}/sdrf/export`),
  downloadSubmission: async (projectId: string) => download(`/projects/${encodeURIComponent(projectId)}/submission/export`),
  experiments: async (projectId?: string) => (await allPages<unknown>(`/experiments${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)).map(normalizeExperiment),
  experimentDeletionPreview: async (id: string): Promise<ExperimentDeletionPreview> => {
    const value = recordValue(await request(`/experiments/${encodeURIComponent(id)}/deletion-preview`))
    return {
      experimentId: textValue(value.experiment_id),
      experimentName: textValue(value.experiment_name),
      runCount: numberValue(value.run_count),
      sourceCount: numberValue(value.source_count),
      derivedCount: numberValue(value.derived_count),
      logicalBytes: numberValue(value.logical_bytes)
    }
  },
  deleteExperiment: async (id: string, confirmation: string) => request(`/experiments/${encodeURIComponent(id)}?confirmation=${encodeURIComponent(confirmation)}`, { method: 'DELETE' }),
  createProject: async (name: string, description?: string) => normalizeProject(await request('/projects', {
    method: 'POST',
    body: JSON.stringify({ name, description: description || null })
  })),
  jobs: async () => (await allPages<unknown>('/jobs')).map(normalizeJob),
  job: async (id: string) => normalizeJob(await request(`/jobs/${encodeURIComponent(id)}`)),
  storage: async () => normalizeList(await request<unknown[] | PaginatedResponse<unknown>>('/storage')).map(normalizeStorage),
  previewStorageReclaim: async (values: { projectId: string, formats: ConversionFormat[] }): Promise<StorageReclaimPreview> => normalizeStorageReclaim(await request('/storage/reclaim/preview', {
    method: 'POST',
    body: JSON.stringify({ scope_type: 'project', scope_ids: [values.projectId], formats: values.formats })
  })),
  reclaimStorage: async (values: { projectId: string, formats: ConversionFormat[] }): Promise<StorageReclaimPreview> => normalizeStorageReclaim(await request('/storage/reclaim', {
    method: 'POST',
    body: JSON.stringify({ scope_type: 'project', scope_ids: [values.projectId], formats: values.formats, confirmation: 'PURGE DERIVED FILES' })
  })),
  importRun: async (values: {
    projectName: string
    experimentName: string
    sampleName: string
    runName: string
    sourcePath?: string
    file?: File
  }) => {
    const projects = await allPages<{ id: string, name: string }>('/projects')
    const project = projects.find(item => item.name === values.projectName) ?? await request<{ id: string, name: string }>('/projects', {
      method: 'POST',
      body: JSON.stringify({ name: values.projectName })
    })
    const experiments = await allPages<{ id: string, name: string }>(`/experiments?project_id=${encodeURIComponent(project.id)}`)
    const experiment = experiments.find(item => item.name === values.experimentName) ?? await request<{ id: string, name: string }>('/experiments', {
      method: 'POST',
      body: JSON.stringify({ project_id: project.id, name: values.experimentName })
    })
    const samples = await allPages<{ id: string, name: string }>(`/samples?experiment_id=${encodeURIComponent(experiment.id)}`)
    const sample = samples.find(item => item.name === values.sampleName) ?? await request<{ id: string, name: string }>('/samples', {
      method: 'POST',
      body: JSON.stringify({ experiment_id: experiment.id, name: values.sampleName })
    })
    const sourceName = values.file?.name ?? values.sourcePath ?? ''
    const lower = sourceName.toLowerCase()
    const sourceClass = lower.endsWith('.mzml') || lower.endsWith('.mzxml')
      ? 'open'
      : lower.endsWith('.mgf') || lower.endsWith('.mgf.gz') || lower.endsWith('.ms2') || lower.endsWith('.ms2.gz') || lower.endsWith('.msp') || lower.endsWith('.msp.gz') ? 'spectrum_list' : 'vendor'
    const run = await request<{ id: string }>('/runs', {
      method: 'POST',
      body: JSON.stringify({
        experiment_id: experiment.id,
        sample_id: sample.id,
        name: values.runName,
        source_class: sourceClass
      })
    })
    if (values.file) {
      const body = new FormData()
      body.set('file', values.file)
      body.set('role', 'source')
      await request(`/runs/${encodeURIComponent(run.id)}/artifacts/upload`, { method: 'POST', body })
    } else if (values.sourcePath) {
      await request(`/runs/${encodeURIComponent(run.id)}/artifacts/import`, {
        method: 'POST',
        body: JSON.stringify({ source_path: values.sourcePath, role: 'source' })
      })
    }
    return { ...run, projectId: project.id }
  },
  retryJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  assignRuns: async (runIds: string[], experimentId: string) => normalizeList(await request<unknown[]>('/runs/bulk-assignment', {
    method: 'POST',
    body: JSON.stringify({ run_ids: runIds, experiment_id: experimentId })
  })).map(item => normalizeRun(item)),
  extractArtifact: async (artifactId: string, force = false) => normalizeJob(await request(`/artifacts/${encodeURIComponent(artifactId)}/extract`, {
    method: 'POST',
    body: JSON.stringify({ extractor: 'spectarr-extractor', schema_version: '1.0', force })
  })),
  spectrum: async (artifactId: string, selection: { msLevel: 1 | 2, index?: number, scanNumber?: number, nativeId?: string }): Promise<SpxtacularSpectrum> => {
    const query = new URLSearchParams({ ms_level: String(selection.msLevel) })
    if (selection.index !== undefined) query.set('index', String(selection.index))
    if (selection.scanNumber !== undefined) query.set('scan_number', String(selection.scanNumber))
    if (selection.nativeId !== undefined) query.set('native_id', selection.nativeId)
    return request(`/artifacts/${encodeURIComponent(artifactId)}/spectrum?${query.toString()}`)
  },
  spectra: async (artifactId: string, selection: { msLevel: 1 | 2, offset?: number, limit?: number, rtSeconds?: number, scanNumber?: number, nativeId?: string, precursorMz?: number }): Promise<SpectrumCatalogPage> => {
    const query = new URLSearchParams({
      ms_level: String(selection.msLevel),
      offset: String(selection.offset ?? 0),
      limit: String(selection.limit ?? 25)
    })
    if (selection.rtSeconds !== undefined) query.set('rt_seconds', String(selection.rtSeconds))
    if (selection.scanNumber !== undefined) query.set('scan_number', String(selection.scanNumber))
    if (selection.nativeId !== undefined) query.set('native_id', selection.nativeId)
    if (selection.precursorMz !== undefined) query.set('precursor_mz', String(selection.precursorMz))
    return request(`/artifacts/${encodeURIComponent(artifactId)}/spectra?${query.toString()}`)
  },
  querySpectra: async (artifactId: string, query: SpectrumQueryRequest): Promise<SpectrumCatalogPage> => request(
    `/artifacts/${encodeURIComponent(artifactId)}/spectra/query`,
    {
      method: 'POST',
      body: JSON.stringify({
        ms_levels: query.msLevels ?? [],
        scan_number_min: query.scanNumberMin,
        scan_number_max: query.scanNumberMax,
        retention_time_min: query.retentionTimeMin,
        retention_time_max: query.retentionTimeMax,
        precursor_mz_min: query.precursorMzMin,
        precursor_mz_max: query.precursorMzMax,
        neutral_mass_min: query.neutralMassMin,
        neutral_mass_max: query.neutralMassMax,
        charges: query.charges ?? [],
        peak_count_min: query.peakCountMin,
        peak_count_max: query.peakCountMax,
        total_ion_current_min: query.totalIonCurrentMin,
        total_ion_current_max: query.totalIonCurrentMax,
        base_peak_mz_min: query.basePeakMzMin,
        base_peak_mz_max: query.basePeakMzMax,
        native_id: query.nativeId,
        polarities: query.polarities ?? [],
        representations: query.representations ?? [],
        sort: query.sort ?? 'retention_time_seconds',
        direction: query.direction ?? 'asc',
        cursor: query.cursor,
        limit: query.limit ?? 50
      })
    }
  ),
  catalogSpectrum: (artifactId: string, entryId: string): Promise<SpxtacularSpectrum> => request(
    `/artifacts/${encodeURIComponent(artifactId)}/spectra/${encodeURIComponent(entryId)}`
  ),
  spectrumCatalogStatus: (artifactId: string): Promise<{ status: 'ready' | 'building' | 'unavailable', spectrum_count: number }> => request(
    `/artifacts/${encodeURIComponent(artifactId)}/spectrum-catalog`
  ),
  generateArtifact: async (runId: string, format: ConversionFormat, inputArtifactId?: string, recipeId?: string) => normalizeJob(await request(`/runs/${encodeURIComponent(runId)}/derivatives`, {
    method: 'POST',
    body: JSON.stringify({ format, input_artifact_id: inputArtifactId, recipe_id: recipeId })
  }))
}
