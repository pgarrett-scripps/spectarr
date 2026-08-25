export type RunStatus = 'ready' | 'processing' | 'warning' | 'failed'
export type ArtifactFormat = 'RAW' | 'mzML' | 'mzXML' | 'MGF' | 'MS2' | 'Parquet'
export type ConversionFormat = 'mzML' | 'mzXML' | 'MGF' | 'MS2'

export type UserRole = 'admin' | 'operator' | 'viewer' | 'service'
export type AuthMode = 'password' | 'local'

export interface AuthConfiguration {
  mode: AuthMode
  localUser?: string
  allowRemoteNoAuth: boolean
}

export interface CurrentUser {
  id: string
  username: string
  displayName: string
  role: UserRole
  active: boolean
}

export interface User extends CurrentUser {
  createdAt: string
  lastLoginAt?: string
}

export interface ApiToken {
  id: string
  name: string
  scopes: string[]
  createdAt: string
  expiresAt?: string
  lastUsedAt?: string
  token?: string
}

export interface ProjectMembership {
  id: string
  projectId: string
  userId: string
  role: UserRole
  createdAt: string
}

export interface ChromatogramPoint {
  time: number
  intensity: number
}

export interface ExtractionSummary {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  extractor: string
  extractorVersion: string
  schemaVersion: string
  sourceSha256: string
  startedAt?: string
  finishedAt?: string
  error?: string
  warnings: string[]
  spectrumCount?: number
  spectraByMsLevel: Record<string, number>
  ms2Count?: number
  durationMinutes?: number
  rtRange?: [number, number]
  mzRange?: [number, number]
  polarities: string[]
  representation?: string
  precursorCount?: number
  chargeCounts: Record<string, number>
  collisionEnergyRange?: [number, number]
  peakCountMean?: number
  ionMobility?: boolean
  diaWindowCount?: number
  tic: ChromatogramPoint[]
  bpc: ChromatogramPoint[]
  raw: Record<string, unknown>
}

export interface InstrumentAgent {
  id: string
  name: string
  status: 'online' | 'offline' | 'degraded' | 'disabled'
  platform: string
  version: string
  watchPaths: string[]
  backlog: number
  lastSeenAt?: string
  lastError?: string
  createdAt: string
  destinationMode: 'inbox' | 'direct'
  destinationExperimentId?: string
}

export interface Instrument {
  id: string
  name: string
  vendor?: string
  model?: string
  serialNumber?: string
  enabled: boolean
}

export interface AutomationRule {
  id: string
  name: string
  enabled: boolean
  scope: 'global' | 'project' | 'instrument'
  projectId?: string
  instrumentId?: string
  generateMzml: boolean
  profileIds: string[]
  extractMetadata: boolean
  deepQc: boolean
  createdAt: string
}

export interface ProcessingProfile {
  id: string
  name: string
  description?: string
  outputFormat: ConversionFormat
  revision: number
  system: boolean
  enabled: boolean
  parameters: {
    preset?: 'sage' | 'biosaur' | 'blitzff' | 'casanovo' | 'casanovo_mgf'
    filters: Array<Record<string, unknown>>
    mzPrecision: 32 | 64
    intensityPrecision: 32 | 64
    compression: 'none' | 'zlib' | 'numpress'
    indexed: boolean
  }
  createdAt: string
  updatedAt: string
}

export interface ProcessingBatchPreview {
  scopeType: 'project' | 'experiments' | 'runs'
  runCount: number
  targetCount: number
  queueCount: number
  currentCount: number
  staleCount: number
  incompatibleCount: number
  queuedCount: number
}

export interface ProcessingBatchItem {
  id: string
  runId: string
  runName: string
  recipeId: string
  recipeName: string
  outputFormat: ConversionFormat
  jobId?: string
  disposition: string
  reason?: string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'cancelled'
  progress: number
  error?: string
}

export interface ProcessingBatch {
  id: string
  scopeType: 'project' | 'experiments' | 'runs'
  scopeIds: string[]
  mode: 'missing' | 'missing_or_stale' | 'force'
  label?: string
  requestedBy?: string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  totalCount: number
  queuedCount: number
  runningCount: number
  succeededCount: number
  failedCount: number
  skippedCount: number
  cancelledCount: number
  progress: number
  createdAt: string
  updatedAt: string
  items: ProcessingBatchItem[]
}

export interface WebhookDestination {
  id: string
  name: string
  url: string
  eventFilters: string[]
  enabled: boolean
  createdAt: string
  signingSecret?: string
}

export interface WebhookDelivery {
  id: string
  destinationId: string
  status: string
  attempts: number
  responseStatus?: number
  lastError?: string
  createdAt: string
}

export interface Project {
  id: string
  name: string
  description?: string
  runCount: number
  sizeBytes: number
  updatedAt: string
  systemKey?: string
  metadata: Record<string, unknown>
  sdrf?: {
    status: 'draft' | 'valid' | 'invalid'
    revision: number
    rowCount: number
    sourceFilename?: string
  }
}

export interface SdrfRow {
  id?: string
  position: number
  values: string[]
  sampleId?: string
  runId?: string
  artifactId?: string
}

export interface SdrfValidationMessage {
  severity: 'error' | 'warning' | 'info'
  code: string
  message: string
  row?: number
  column?: number
}

export interface SdrfValidationReport {
  valid: boolean
  engine: string
  ontology: boolean
  errorCount: number
  warningCount: number
  messages: SdrfValidationMessage[]
}

export interface SdrfDocument {
  id: string
  projectId: string
  specificationVersion: string
  templates: string[]
  columns: string[]
  rows: SdrfRow[]
  status: 'draft' | 'valid' | 'invalid'
  revision: number
  sourceFilename?: string
  contentSha256?: string
  validationEngine?: string
  validationReport?: SdrfValidationReport
  createdAt: string
  updatedAt: string
}

export interface SdrfTemplate {
  name: string
  version: string
  kind: string
}

export interface SubmissionPreview {
  projectId: string
  sourceCount: number
  derivativeCount: number
  totalBytes: number
  sdrfStatus: string
  sdrfRevision?: number
  mappedRows: number
  unmappedRows: number
  ready: boolean
}

export interface Experiment {
  id: string
  projectId: string
  name: string
  description?: string
  intakeAgentId?: string
}

export interface ExperimentDeletionPreview {
  experimentId: string
  experimentName: string
  runCount: number
  sourceCount: number
  derivedCount: number
  logicalBytes: number
}

export interface StorageReclaimPreview {
  artifactCount: number
  reclaimableBytes: number
  formatCounts: Record<string, number>
  blockedCount: number
}

export interface Artifact {
  id: string
  name: string
  format: ArtifactFormat
  role: 'source' | 'derived' | 'preview' | 'analysis_result' | 'attachment'
  sizeBytes: number
  checksum: string
  status: 'verified' | 'generating' | 'failed' | 'purged'
  libraryPath?: string
  materializationMode?: 'hardlink' | 'copy'
}

export interface Run {
  id: string
  projectId?: string
  experimentId?: string
  name: string
  projectName: string
  experimentName: string
  sampleName: string
  instrument: string
  acquiredAt: string
  importedAt: string
  status: RunStatus
  sourceFormat: ArtifactFormat
  sizeBytes: number
  spectraCount?: number
  ms2Count?: number
  durationMinutes?: number
  extraction?: ExtractionSummary
  metadata: Record<string, unknown>
  artifacts: Artifact[]
  assignmentStatus: 'needs_assignment' | 'assigned'
}

export interface Job {
  id: string
  kind: 'import' | 'ingest' | 'convert' | 'index' | 'verify' | 'extract_metadata' | 'preview'
  runName: string
  status: 'queued' | 'running' | 'complete' | 'failed'
  progress: number
  detail: string
  createdAt: string
}

export interface StorageLocation {
  id: string
  name: string
  path: string
  kind: 'filesystem' | 's3'
  usedBytes: number
  capacityBytes: number
  status: 'healthy' | 'warning' | 'offline'
  artifactCount: number
}

export interface SystemHealth {
  api: 'online' | 'offline'
  workers: number
  queueDepth: number
  version: string
}

export interface OverviewData {
  runs: Run[]
  jobs: Job[]
  projects: Project[]
  storage: StorageLocation[]
  health: SystemHealth
  stats: {
    runs: number
    artifacts: number
    formatCounts: Record<string, number>
  }
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}
