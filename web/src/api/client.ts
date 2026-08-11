import type { components, paths } from './schema'

/**
 * The one place the browser talks to the archive.
 *
 * Every type here comes from `schema.ts`, which is generated from the backend's
 * own OpenAPI document. Nothing in this directory declares what a track looks
 * like: a hand-written interface that drifts from the server is a bug that
 * type-checks, and it is the exact failure this project spends its architecture
 * avoiding elsewhere.
 */

export type Track = components['schemas']['TrackResponse']
export type TrackList = components['schemas']['TrackListResponse']
export type AvailableYears = components['schemas']['AvailableYearsResponse']
export type SystemInfo = components['schemas']['SystemInfoResponse']
export type TrackMetadata = components['schemas']['UserMetadataResponse']
export type MetadataUpdate = components['schemas']['TrackMetadataRequest']
export type TrackAnalysis = components['schemas']['TrackAnalysisResponse']
export type TrackProfile = components['schemas']['TrackProfileResponse']
export type ProfileSample = components['schemas']['ProfileSampleResponse']
export type Geometry = components['schemas']['GeometryResponse']
export type YearStatistics = components['schemas']['YearResponse']
export type MonthlyStatistics = components['schemas']['MonthlyResponse']
export type Totals = components['schemas']['TotalsResponse']
export type Activity = components['schemas']['Activity']
export type TrackKind = components['schemas']['TrackKind']
export type AnalysisAvailability = components['schemas']['AnalysisAvailability']
export type AggregationScope = components['schemas']['AggregationScope']
export type TemporalEvidence = components['schemas']['TemporalEvidence']
export type TrackOrder = components['schemas']['TrackOrder']

export type InstalledMaps = components['schemas']['InstalledMapsResponse']
export type InstalledMap = components['schemas']['InstalledMapResponse']
export type MapCatalog = components['schemas']['CatalogResponse']
export type MapCatalogEntry = components['schemas']['CatalogEntryResponse']
export type MapCoverage = components['schemas']['CoverageResponse']
export type MapSource = components['schemas']['MapSourceResponse']
export type MapAttribution = components['schemas']['AttributionResponse']
export type MapJob = components['schemas']['JobResponse']
export type MapJobs = components['schemas']['JobsResponse']
export type MapCredits = components['schemas']['CreditsResponse']
export type MapBounds = components['schemas']['BoundsResponse']

export type TrackListQuery = NonNullable<
  paths['/api/v1/tracks']['get']['parameters']['query']
>

/** An error the archive answered with, carrying its stable code. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

function search(query: Query): string {
  const parameters = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      parameters.set(key, String(value))
    }
  }
  const rendered = parameters.toString()
  return rendered ? `?${rendered}` : ''
}

async function get<T>(path: string, query: Query = {}, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1${path}${search(query)}`, {
    headers: { accept: 'application/json' },
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) {
    throw await apiError(response)
  }
  return (await response.json()) as T
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) throw await apiError(response)
  return (await response.json()) as T
}

async function apiError(response: Response): Promise<ApiError> {
  // The archive answers one stable envelope. Anything else is a proxy, a
  // gateway or a bug, and it is reported as what it is rather than guessed at.
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string }
      detail?: { error?: { code?: string; message?: string } }
    }
    // FastAPI wraps a raised `HTTPException` detail; the routers that raise one
    // put the archive's own envelope inside it. Both shapes are the same
    // envelope, and a reader should not be able to tell which route answered.
    const envelope = body.error ?? body.detail?.error
    if (envelope?.code) {
      return new ApiError(response.status, envelope.code, envelope.message ?? 'request failed')
    }
  } catch {
    /* not a JSON body */
  }
  return new ApiError(response.status, 'unexpected_response', `request failed (${response.status})`)
}

export const api = {
  listTracks: (query: TrackListQuery, signal?: AbortSignal) =>
    get<TrackList>('/tracks', query, signal),
  readTrack: (id: number, signal?: AbortSignal) => get<Track>(`/tracks/${id}`, {}, signal),
  readAnalysis: (id: number, signal?: AbortSignal) =>
    get<TrackAnalysis>(`/tracks/${id}/analysis`, {}, signal),
  readProfile: (id: number, maxSamples: number, signal?: AbortSignal) =>
    get<TrackProfile>(`/tracks/${id}/profile`, { max_samples: maxSamples }, signal),
  readGeometry: (id: number, maxPoints: number, signal?: AbortSignal) =>
    get<Geometry>(`/tracks/${id}/geometry`, { max_points: maxPoints }, signal),
  readYear: (year: number, query: Query, signal?: AbortSignal) =>
    get<YearStatistics>(`/statistics/year/${year}`, query, signal),
  readMonthly: (year: number, query: Query, signal?: AbortSignal) =>
    get<MonthlyStatistics>(`/statistics/year/${year}/monthly`, query, signal),
  // Which years exist is the archive's answer, not a range generated from the
  // reader's clock: an archive nobody has added to since 2019 must not open on
  // an empty current year.
  readYears: (query: Query, signal?: AbortSignal) =>
    get<AvailableYears>('/statistics/years', query, signal),
  readSystemInfo: (signal?: AbortSignal) => get<SystemInfo>('/system/info', {}, signal),

  // --- Offline maps ---------------------------------------------------------
  //
  // `readCoverage` is the only one of these a track page calls, and it reaches
  // no provider. Everything else is a management action somebody asked for.

  readCoverage: (bbox: string, signal?: AbortSignal) =>
    get<MapCoverage>('/maps/coverage', { bbox }, signal),
  readInstalledMaps: (signal?: AbortSignal) => get<InstalledMaps>('/maps', {}, signal),
  readMapCatalog: (parent: string | null, signal?: AbortSignal) =>
    get<MapCatalog>('/maps/catalog', parent ? { parent } : {}, signal),
  readMapJobs: (signal?: AbortSignal) => get<MapJobs>('/maps/jobs', {}, signal),
  readMapCredits: (signal?: AbortSignal) => get<MapCredits>('/maps/credits', {}, signal),

  async refreshMapCatalog(): Promise<MapCatalog> {
    return post<MapCatalog>('/maps/catalog/refresh')
  },

  /**
   * Install or update one region.
   *
   * A region, never an address. The endpoint has no field that could name a
   * host, which is what stops a self-hosted archive becoming a fetcher operated
   * by whoever can reach it.
   */
  async installMap(regionId: string): Promise<MapJob> {
    return post<MapJob>('/maps/install', { region_id: regionId })
  },

  async cancelMapJob(jobId: string): Promise<MapJob> {
    return post<MapJob>(`/maps/jobs/${encodeURIComponent(jobId)}/cancel`)
  },

  async removeMap(regionId: string): Promise<void> {
    const response = await fetch(`/api/v1/maps/regions/${regionId}`, {
      method: 'DELETE',
      headers: { accept: 'application/json' },
    })
    if (!response.ok) throw await apiError(response)
  },

  async setKind(id: number, kind: TrackKind): Promise<Track> {
    const response = await fetch(`/api/v1/tracks/${id}/classification`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ kind }),
    })
    if (!response.ok) throw await apiError(response)
    return (await response.json()) as Track
  },

  async resetKind(id: number): Promise<Track> {
    const response = await fetch(`/api/v1/tracks/${id}/classification`, {
      method: 'DELETE',
      headers: { accept: 'application/json' },
    })
    if (!response.ok) throw await apiError(response)
    return (await response.json()) as Track
  },

  /**
   * Correct what the user says about a track.
   *
   * A partial update: only the fields in `update` are sent, so renaming a track
   * never touches its note. `null` clears a field and letting the source title
   * speak again is what clearing the title means.
   */
  async updateMetadata(id: number, update: MetadataUpdate): Promise<Track> {
    const response = await fetch(`/api/v1/tracks/${id}/metadata`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify(update),
    })
    if (!response.ok) throw await apiError(response)
    return (await response.json()) as Track
  },
}
