import { vi } from 'vitest'
import type {
  AutomaticImport,
  AvailableYears,
  Geometry,
  ImportScan,
  InstalledMap,
  InstalledMaps,
  MapAttribution,
  MapCatalog,
  MapCatalogEntry,
  MapCoverage,
  MapJob,
  MapSource,
  MonthlyStatistics,
  OverallStatistics,
  SystemInfo,
  Totals,
  Track,
  TrackAnalysis,
  TrackList,
  TrackProfile,
  YearStatistics,
} from './api/client'

/**
 * The shapes the archive answers with, built once for every component test.
 *
 * They are constructed from the generated API types rather than declared here,
 * so a field the backend adds is a compile error in the fixture rather than a
 * test that keeps passing against a payload the server stopped sending.
 */

export function totals(overrides: Partial<Totals> = {}): Totals {
  return {
    track_count: 0,
    analysed_track_count: 0,
    tracks_without_analysis: 0,
    tracks_with_outdated_analysis: 0,
    tracks_with_invalid_analysis: 0,
    tracks_with_failed_analysis: 0,
    tracks_without_observed_timing: 0,
    distance_m: 0,
    elapsed_duration_s: 0,
    moving_duration_s: 0,
    elevation_gain_m: 0,
    ...overrides,
  }
}

export function year(overrides: Partial<Totals> = {}, unplaced?: YearStatistics['unplaced']) {
  const statistics: YearStatistics = {
    year: 2025,
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    totals: totals(overrides),
    unplaced: unplaced ?? { without_date: totals(), with_unverified_date: totals() },
  }
  return statistics
}

export function monthly(months: Partial<Totals>[] = []): MonthlyStatistics {
  const filled = Array.from({ length: 12 }, (_, index) => months[index] ?? {})
  return {
    year: 2025,
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    months: filled.map((overrides, index) => ({
      month: index + 1,
      totals: totals(overrides),
      by_activity: [],
    })),
    activities: [],
  }
}

export function overall(overrides: Partial<OverallStatistics> = {}): OverallStatistics {
  return {
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    totals: totals(),
    unplaced: { without_date: totals(), with_unverified_date: totals() },
    years: [],
    activities: [],
    ...overrides,
  }
}

export function availableYears(overrides: Partial<AvailableYears> = {}): AvailableYears {
  return {
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    years: [2025],
    unplaced: { without_date: 0, with_unverified_date: 0 },
    archive_track_count: 1,
    ...overrides,
  }
}

export function track(overrides: Partial<Track> = {}): Track {
  return {
    id: 7,
    raw_import_sha256: 'a'.repeat(64),
    source_index: 0,
    title: 'Talaia',
    metadata: {
      title: null,
      note: null,
      source_title: 'Talaia',
      is_overridden: false,
    },
    activity: 'walking',
    classification: {
      effective_kind: 'recorded',
      detected_kind: 'recorded',
      confidence: 0.95,
      method: 'evidence-weights',
      method_version: '2',
      evidence: ['timestamps_present', 'gps_accuracy_present'],
      override: null,
      is_overridden: false,
    },
    timeline: {
      started_at: '2025-10-20T07:00:00Z',
      ended_at: '2025-10-20T12:42:11Z',
      basis: 'observed',
      is_actual_calendar_time: true,
      is_actual_activity_timing: true,
    },
    analysis: {
      status: 'current',
      distance_m: 9270.7,
      elevation_gain_m: 508.4,
      elapsed_duration_s: 20531,
      moving_duration_s: 10015,
    },
    point_count: 831,
    segment_count: 1,
    geometry_sha256: 'f'.repeat(64),
    same_recording_ids: [],
    approximate_location: null,
    source: {
      exchange_format: 'gpx',
      format_version: '1.1',
      creator: 'synthetic',
      links: [],
      extension_namespaces: [],
    },
    ...overrides,
  }
}

export function trackList(tracks: Track[], overrides: Partial<TrackList> = {}): TrackList {
  return { total: tracks.length, limit: 25, offset: 0, tracks, ...overrides }
}

export function geometry(overrides: Partial<Geometry> = {}): Geometry {
  return {
    track_id: 7,
    segment_count: 1,
    point_count: 2,
    total_point_count: 831,
    simplified: true,
    // The identity of the line this answer draws, which the archive states and
    // nothing here recomputes. A test that wants a *different* drawing says so
    // by overriding this, exactly as the archive would.
    shape_sha256: 'a'.repeat(64),
    segments: [
      {
        points: [
          { latitude: 39.6, longitude: 2.8, elevation: 12, time: null },
          { latitude: 39.7, longitude: 2.9, elevation: 520, time: null },
        ],
      },
    ],
    ...overrides,
  }
}

export function analysis(overrides: Partial<TrackAnalysis> = {}): TrackAnalysis {
  return {
    track_id: 7,
    status: 'current',
    analyzed_at: '2026-08-09T10:00:00Z',
    profile: {
      distance_algorithm: 'haversine',
      distance_algorithm_version: 1,
      movement_algorithm: 'windowed-extent',
      movement_algorithm_version: 2,
      elevation_algorithm: 'median-deadband',
      elevation_algorithm_version: 2,
      metric_schema_version: 1,
    },
    geometry: {
      distance_m: 9270.7,
      elevation_min_m: 12.0,
      elevation_max_m: 520.4,
      elevation_gain_m: 508.4,
      elevation_loss_m: 505.1,
    },
    timed_path: {
      basis: 'observed',
      is_actual_activity_timing: true,
      elapsed_duration_s: 20531,
      moving_duration_s: 10015,
      stopped_duration_s: 4000,
      unobserved_gap_duration_s: 6000,
      unattributed_duration_s: 516,
      average_speed_mps: 0.45,
      moving_average_speed_mps: 0.93,
      maximum_sustained_speed_mps: 1.8,
    },
    quality: [],
    error_code: null,
    ...overrides,
  }
}

export function profile(overrides: Partial<TrackProfile> = {}): TrackProfile {
  return {
    track_id: 7,
    sample_count: 2,
    total_sample_count: 831,
    total_distance_m: 9270.7,
    analysis_status: 'current',
    derived_with: analysis().profile ?? {
      distance_algorithm: 'haversine',
      distance_algorithm_version: 1,
      movement_algorithm: 'windowed-extent',
      movement_algorithm_version: 2,
      elevation_algorithm: 'median-deadband',
      elevation_algorithm_version: 2,
      metric_schema_version: 1,
    },
    timing_basis: 'observed',
    is_actual_activity_timing: true,
    segments: [
      {
        index: 0,
        samples: [
          {
            segment_index: 0,
            point_index: 0,
            distance_m: 0,
            latitude: 39.6,
            longitude: 2.8,
            time: '2025-10-20T07:00:00Z',
            elevation_m: 12,
            filtered_elevation_m: 12,
            speed_mps: 0.9,
            heart_rate_bpm: null,
            cadence_rpm: null,
          },
          {
            segment_index: 0,
            point_index: 1,
            distance_m: 9270.7,
            latitude: 39.7,
            longitude: 2.9,
            time: '2025-10-20T12:42:11Z',
            elevation_m: 520,
            filtered_elevation_m: 519,
            speed_mps: 1.1,
            heart_rate_bpm: null,
            cadence_rpm: null,
          },
        ],
      },
    ],
    ...overrides,
  }
}

export function systemInfo(overrides: Partial<SystemInfo> = {}): SystemInfo {
  return {
    version: '0.1.0',
    schema_version: 6,
    timezone: 'UTC',
    processing: [
      {
        importer: 'gpx',
        importer_version: '2',
        normalization_schema_version: 1,
        classifier: 'evidence-weights',
        classifier_version: '2',
      },
    ],
    analysis: {
      distance_algorithm: 'haversine',
      distance_algorithm_version: 1,
      movement_algorithm: 'windowed-extent',
      movement_algorithm_version: 2,
      elevation_algorithm: 'median-deadband',
      elevation_algorithm_version: 2,
      metric_schema_version: 1,
    },
    upload_enabled: true,
    ...overrides,
  }
}

export function automaticImport(overrides: Partial<AutomaticImport> = {}): AutomaticImport {
  return {
    enabled: true,
    directory: '/import',
    interval_minutes: 15,
    settle_minutes: 5,
    scanning: false,
    last_scan: importScan(),
    last_activity: null,
    next_scan_at: '2026-09-19T12:30:00Z',
    ...overrides,
  }
}

export function importScan(overrides: Partial<ImportScan> = {}): ImportScan {
  return {
    started_at: '2026-09-19T12:15:00Z',
    finished_at: '2026-09-19T12:15:01Z',
    directory_available: true,
    discovered: 4,
    imported: 0,
    repaired: 0,
    skipped: 4,
    failed: 0,
    waiting: 0,
    failures: [],
    ...overrides,
  }
}

export interface StubbedArchive {
  /** Every URL the page asked for, in order. */
  readonly requested: string[]
  /** Every request that was not a plain read, so a test can assert what it sent. */
  readonly writes: { url: string; method: string; body: unknown }[]
}

export type Route = (url: string) => unknown

/**
 * Answer the archive's endpoints from fixtures.
 *
 * The routes are matched in order and the first one that returns something
 * answers. A request nothing matches is a 404 with the archive's own error
 * envelope -- which is what the real server does, and what a page has to cope
 * with.
 */
export function stubArchive(routes: Route[]): StubbedArchive {
  const requested: string[] = []
  const writes: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      requested.push(url)
      const method = init?.method ?? 'GET'
      if (method !== 'GET') {
        writes.push({
          url,
          method,
          body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
        })
      }
      for (const route of routes) {
        const body = route(url)
        if (body !== undefined) return Promise.resolve(json(body))
      }
      return Promise.resolve(
        json({ error: { code: 'track_not_found', message: 'no such track' } }, 404),
      )
    }),
  )
  return { requested, writes }
}

/** A route that answers one URL fragment with a body. */
export function on(fragment: string, body: unknown): Route {
  return (url) => (url.includes(fragment) ? body : undefined)
}

/** A route that fails, so a page's error and retry states can be exercised. */
export function failing(fragment: string, message = 'network down'): Route {
  return (url) => {
    if (!url.includes(fragment)) return undefined
    throw new TypeError(message)
  }
}

/**
 * Report every observed element as on screen.
 *
 * The suite's default observer never intersects, so a component that defers
 * work until it is visible does nothing in a test that does not ask for this.
 * A test about *what happens once a row is read* says so with this line.
 */
export function everythingIsVisible(): void {
  class VisibleObserver implements IntersectionObserver {
    readonly root = null
    readonly rootMargin = ''
    readonly thresholds: readonly number[] = []

    constructor(private readonly notify: IntersectionObserverCallback) {}

    observe(element: Element): void {
      this.notify([{ isIntersecting: true, target: element } as IntersectionObserverEntry], this)
    }

    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  }
  vi.stubGlobal('IntersectionObserver', VisibleObserver)
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

// --- Offline maps ------------------------------------------------------------

export function mapAttribution(
  overrides: Partial<MapAttribution> = {},
): MapAttribution {
  return {
    data_owner: 'OpenStreetMap contributors',
    provider: 'Geofabrik GmbH',
    license_identifier: 'ODbL-1.0',
    license_name: 'Open Database License 1.0',
    required_text: 'Map data © OpenStreetMap contributors',
    links: [{ label: 'OpenStreetMap', url: 'https://www.openstreetmap.org/copyright' }],
    ...overrides,
  }
}

/**
 * The content hash the fixture package is delivered under.
 *
 * A real one: sixty-four hex digits, with the source identity and the tile
 * template both derived from it exactly as the archive derives them. A fixture
 * whose identities do not agree with each other cannot fail a test that depends
 * on their agreeing.
 */
export const DELIVERY_ID = `abc123def456${'0'.repeat(52)}`

export function mapSource(overrides: Partial<MapSource> = {}): MapSource {
  const delivery = overrides.delivery_id ?? DELIVERY_ID
  return {
    region_id: 'geofabrik:europe/monaco',
    region_name: 'Monaco',
    source_id: `map-${delivery.slice(0, 12)}`,
    delivery_id: delivery,
    tiles_url: `/api/v1/maps/tiles/${delivery}/{z}/{x}/{y}.mvt`,
    tile_schema: 'shortbread',
    tile_schema_version: '1.0',
    bounds: {
      min_longitude: 7.4,
      min_latitude: 43.48,
      max_longitude: 7.6,
      max_latitude: 43.76,
    },
    min_zoom: 0,
    max_zoom: 14,
    attribution: mapAttribution(),
    ...overrides,
  }
}

export function coverage(overrides: Partial<MapCoverage> = {}): MapCoverage {
  return {
    sources: [],
    glyphs_url: '/fonts/{fontstack}/{range}.pbf',
    any_installed: false,
    suggestions: [],
    catalog_known: false,
    ...overrides,
  }
}

export function installedMap(overrides: Partial<InstalledMap> = {}): InstalledMap {
  return {
    region_id: 'geofabrik:europe/monaco',
    region_name: 'Monaco',
    provider: 'geofabrik',
    state: 'installed',
    format: 'mbtiles',
    tile_schema: 'shortbread',
    tile_schema_version: '1.0',
    delivery_id: DELIVERY_ID,
    size_bytes: 1_720_320,
    bounds: {
      min_longitude: 7.4,
      min_latitude: 43.48,
      max_longitude: 7.6,
      max_latitude: 43.76,
    },
    min_zoom: 0,
    max_zoom: 14,
    dataset_version: '1.0',
    dataset_timestamp: '2026-08-08T02:40:56+00:00',
    downloaded_at: '2026-08-09T12:00:00+00:00',
    attribution: mapAttribution(),
    ...overrides,
  }
}

export function installedMaps(overrides: Partial<InstalledMaps> = {}): InstalledMaps {
  return {
    maps: [],
    installs_enabled: true,
    total_size_bytes: 0,
    ...overrides,
  }
}

export function catalogEntry(overrides: Partial<MapCatalogEntry> = {}): MapCatalogEntry {
  return {
    region_id: 'geofabrik:europe/monaco',
    name: 'Monaco',
    parent_id: 'geofabrik:europe',
    has_children: false,
    installable: true,
    availability_known: true,
    package: { size_bytes: 1_720_320, updated_at: '2026-08-08T02:40:56+00:00' },
    installed: false,
    ...overrides,
  }
}

export function mapCatalog(overrides: Partial<MapCatalog> = {}): MapCatalog {
  return {
    parent_id: null,
    provider: 'geofabrik',
    provider_name: 'Geofabrik GmbH',
    provider_available: true,
    fetched_at: '2026-08-09T11:00:00+00:00',
    entries: [],
    ...overrides,
  }
}

export function mapJob(overrides: Partial<MapJob> = {}): MapJob {
  return {
    job_id: 'job-1',
    region_id: 'geofabrik:europe/monaco',
    region_name: 'Monaco',
    state: 'downloading',
    bytes_downloaded: 324_000_000,
    bytes_total: 810_000_000,
    percentage: 40,
    is_update: false,
    error_code: null,
    started_at: '2026-08-09T12:00:00+00:00',
    updated_at: '2026-08-09T12:01:00+00:00',
    ...overrides,
  }
}
