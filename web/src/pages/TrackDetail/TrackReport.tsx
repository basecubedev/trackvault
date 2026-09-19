import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { api, type Track, type TrackKind } from '../../api/client'
import { evidenceLeaning, explainEvidence, explainQuality } from '../../api/explanations'
import {
  formatDistance,
  formatDuration,
  formatElevation,
  formatInstantWithTime,
  formatSpeed,
} from '../../api/format'
import { analysisLabel, durationHeading, kindLabel, timingLabel } from '../../api/labels'
import { displayTitle } from '../../api/titles'
import { useRequest } from '../../api/useRequest'
import { LazyChart } from '../../charts/LazyChart'
import { Badge } from '../../components/Badge'
import { Metric } from '../../components/Metric'
import { Notice } from '../../components/Notice'
import { boundsOf, boundsQuery, profileToFeatureCollection } from '../../map/geojson'
import { MapOffer } from '../../map/MapOffer'
import { MAP_THEMES, type MapTheme } from '../../map/style'
import { TrackMap } from '../../map/TrackMap'
import { WidgetBoard } from '../../layout/WidgetBoard'
import { useHovered, useHoverStore } from './hover'
import { useTrackLayout } from './layout'
import { TitleEditor } from './TitleEditor'
import { DEFAULT_TRACK_LAYOUT, TRACK_WIDGETS } from './widgets'
import { usePrefersDark } from '../../charts/theme'
import { flatten, hasSensorReadings, profileOption, sampleAt, sensorOption } from './profileChart'

const PROFILE_SAMPLES = 2000
/**
 * How many samples the chart asks for.
 *
 * More than a screen can distinguish and far fewer than a long recording holds.
 * The archive bounds it anyway; asking for a number this page can actually draw
 * keeps the response small enough to arrive quickly on a phone.
 */

/**
 * Everything the archive can say about one track.
 *
 * This is the track's own page *and* what a row in the list opens, and that is
 * deliberate: two renderings of one track would be two answers to every
 * question this project spends its architecture giving one answer to. What
 * differs between the two places is where it sits in the document -- so the
 * heading level is a parameter and nothing else is.
 *
 * The order is the order somebody reads it in -- what the track is, then its
 * numbers, then its shape, then how the archive decided what it decided, and
 * only then the provenance and the algorithm versions. A diagnostics dump with
 * the interesting part at the bottom is what this replaced.
 *
 * The map and the chart are drawn from the *same* samples, so a chart cursor
 * and a map marker address one position by `(segment_index, point_index)`
 * rather than by the browser searching for a nearby coordinate.
 *
 * Below the title, the badges and the notices, the report is a board of
 * widgets the owner can arrange. What the report *says* is not arrangeable:
 * the title, the state badges and every caveat stay above the board, where no
 * rearrangement can separate a number from the warning that qualifies it.
 */
export function TrackReport({
  trackId: id,
  headingLevel = 1,
  onChanged,
  customizable = false,
}: {
  trackId: number
  /** 1 on the track's own page, 2 where a page of its own already has one. */
  headingLevel?: 1 | 2
  /**
   * Whether the owner may rearrange the report here. On the track's own page;
   * not in a list row, where the same arrangement is drawn but not edited.
   */
  customizable?: boolean
  /**
   * Fired when a correction changed something a caller may be showing too.
   *
   * A kind corrected in a list row must not leave the row above it claiming the
   * old one. What changed is the archive's answer, so the caller is told to ask
   * again rather than handed a value to copy.
   */
  onChanged?: () => void
}) {
  const [override, setOverride] = useState<Track | null>(null)
  const [showRaw, setShowRaw] = useState(false)
  const [theme, setTheme] = useState<MapTheme>('outdoor')
  const hover = useHoverStore()
  const layout = useTrackLayout()

  const track = useRequest((signal) => api.readTrack(id, signal), [id])
  const analysis = useRequest((signal) => api.readAnalysis(id, signal), [id, override])
  const profile = useRequest((signal) => api.readProfile(id, PROFILE_SAMPLES, signal), [id])

  const current = override ?? track.data
  const flat = useMemo(() => (profile.data ? flatten(profile.data) : []), [profile.data])
  const positions = useMemo(
    () =>
      flat.flatMap((entry) =>
        entry.sample === null
          ? []
          : [
              {
                segment_index: entry.sample.segment_index,
                point_index: entry.sample.point_index,
                latitude: entry.sample.latitude,
                longitude: entry.sample.longitude,
              },
            ],
      ),
    [flat],
  )
  const hasSpeed = useMemo(
    () =>
      flat.some((entry) => entry.sample?.speed_mps !== null && entry.sample?.speed_mps !== undefined),
    [flat],
  )
  const dark = usePrefersDark()
  const option = useMemo(
    () => profileOption(flat, { showRawElevation: showRaw, showSpeed: hasSpeed, dark }),
    [flat, showRaw, hasSpeed, dark],
  )
  // Only where a sensor actually measured something. An empty chart with two
  // axes and no line is a page saying it has data it does not have.
  const sensors = useMemo(() => hasSensorReadings(flat), [flat])
  const sensorChart = useMemo(() => sensorOption(flat, { dark }), [flat, dark])
  const collection = useMemo(
    () =>
      profile.data
        ? profileToFeatureCollection(profile.data)
        : { type: 'FeatureCollection' as const, features: [] },
    [profile.data],
  )

  // The rectangle the track occupies, as a query string, or `null` while there
  // is no geometry yet.
  const bbox = useMemo(() => {
    const bounds = boundsOf(collection)
    return bounds === null ? null : boundsQuery(bounds)
  }, [collection])

  // The one map request a track view makes, and it reaches no provider: the
  // archive answers from what is installed here.
  const coverage = useRequest(
    (signal) => (bbox === null ? Promise.resolve(null) : api.readCoverage(bbox, signal)),
    [bbox],
  )

  const correct = useCallback(
    async (kind: TrackKind | null) => {
      const updated = kind === null ? await api.resetKind(id) : await api.setKind(id, kind)
      // The server is the authority on what the correction changed, so the
      // view takes its answer rather than predicting one and refetches
      // everything the correction can move.
      setOverride(updated)
      track.reload()
      analysis.reload()
      onChanged?.()
    },
    [id, track, analysis, onChanged],
  )

  const Section = headingLevel === 1 ? 'h2' : 'h3'
  const Sub = headingLevel === 1 ? 'h3' : 'h4'

  if (track.error !== null) {
    return (
      <TrackUnavailable
        message={track.error}
        onRetry={track.reload}
        headingLevel={headingLevel}
      />
    )
  }
  if (!current) {
    return (
      <p className="muted" role="status" data-testid="request-loading">
        Loading this track…
      </p>
    )
  }

  const timing = timingLabel(current.timeline.basis)
  const status = analysisLabel(current.analysis.status)
  const metrics = analysis.data
  const usable = current.analysis.status === 'current'

  // Every widget the board can place, already rendered. The board moves the
  // frames around them and never these: a drag re-renders nothing in here.
  const widgets: Record<string, ReactNode> = {
    'metric.distance': (
      <Metric
        label="Distance"
        testId="detail-distance"
        value={formatDistance(usable ? metrics?.geometry.distance_m : null)}
      />
    ),
    'metric.elevation-gain': (
      <Metric
        label="Elevation gain"
        value={formatElevation(usable ? metrics?.geometry.elevation_gain_m : null)}
      />
    ),
    'metric.moving': (
      <Metric
        label={durationHeading('Moving', current.timeline.is_actual_activity_timing)}
        value={formatDuration(usable ? metrics?.timed_path.moving_duration_s : null)}
      />
    ),
    'metric.elapsed': (
      <Metric
        label={durationHeading('Elapsed', current.timeline.is_actual_activity_timing)}
        value={formatDuration(usable ? metrics?.timed_path.elapsed_duration_s : null)}
        note={timing.text}
      />
    ),
    'metric.moving-average': (
      <Metric
        label={durationHeading('Moving average', current.timeline.is_actual_activity_timing)}
        value={formatSpeed(usable ? metrics?.timed_path.moving_average_speed_mps : null)}
      />
    ),
    'metric.maximum-sustained': (
      <Metric
        label={durationHeading('Maximum sustained', current.timeline.is_actual_activity_timing)}
        value={formatSpeed(usable ? metrics?.timed_path.maximum_sustained_speed_mps : null)}
      />
    ),
    map: (
      <section className="panel panel--fill">
        <Section>Where it went</Section>
        {profile.data === null ? (
          <p className="chart-fallback">
            {profile.error ?? 'Loading the shape of this track…'}
            {profile.error !== null && (
              <>
                {' '}
                <button type="button" onClick={profile.reload}>
                  Retry
                </button>
              </>
            )}
          </p>
        ) : (
          <TrackMap
            collection={collection}
            samples={positions}
            coverage={coverage.data}
            theme={theme}
            hover={hover}
          />
        )}
        <div className="filters">
          <div className="field">
            <label htmlFor={`map-theme-${String(id)}`}>Basemap</label>
            <select
              id={`map-theme-${String(id)}`}
              value={theme}
              data-testid="map-theme"
              onChange={(event) => {
                setTheme(event.target.value as MapTheme)
              }}
            >
              {MAP_THEMES.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p className="muted">
          {collection.features.length} segment{collection.features.length === 1 ? '' : 's'}, drawn
          separately.
        </p>
        {theme !== 'none' && coverage.data !== null && coverage.data.sources.length === 0 && (
          <MapOffer coverage={coverage.data} />
        )}
        {coverage.data !== null && coverage.data.sources.length > 0 && (
          <p className="map-attribution" data-testid="map-attribution">
            {coverage.data.sources.map((source) => source.attribution.required_text).join(' · ')}
            {' — packaged by '}
            {coverage.data.sources[0]?.attribution.provider}
            {', '}
            {coverage.data.sources[0]?.attribution.license_name}
          </p>
        )}
      </section>
    ),
    profile: (
      <section className="panel panel--fill">
        <Section>Elevation and speed</Section>
        <div className="filters">
          <div className="field">
            <label htmlFor={`elevation-series-${String(id)}`}>Elevation</label>
            <select
              id={`elevation-series-${String(id)}`}
              value={showRaw ? 'both' : 'filtered'}
              onChange={(event) => {
                setShowRaw(event.target.value === 'both')
              }}
            >
              <option value="filtered">Filtered</option>
              <option value="both">Filtered and raw</option>
            </select>
          </div>
        </div>
        {profile.data === null ? (
          <p className="chart-fallback">{profile.error ?? 'Loading…'}</p>
        ) : (
          <>
            <LazyChart
              height="fill"
              option={option}
              hover={hover}
              label={`Elevation and speed of ${displayTitle(current)} against distance`}
            />
            {!hasSpeed && (
              <p className="muted" data-testid="no-speed">
                Speed unavailable for this track.
              </p>
            )}
            <HoverReadout store={hover} samples={flat} />
            <p className="muted">
              {profile.data.sample_count} of {profile.data.total_sample_count} positions shown.
              The chart keeps every turning point and both extremes, so the summit is the summit.
            </p>
          </>
        )}
      </section>
    ),
    sensors: sensors ? (
      <section className="panel panel--fill" data-testid="sensor-panel">
        <Section>Heart rate and cadence</Section>
        <LazyChart
          height="fill"
          option={sensorChart}
          hover={hover}
          label={`Heart rate and cadence of ${displayTitle(current)} against distance`}
        />
        <p className="muted">
          What the sensors reported, passed through as measured. A gap is a reading the sensor
          missed; a cadence of zero is a reading.
        </p>
      </section>
    ) : null,
    classification: (
      <section className="panel">
        <Section>What kind of track this is</Section>
        <p className="muted">
          The archive weighed what the document actually showed. A correction below outranks it
          from now on and survives reprocessing — but it says what the track <em>is</em>, not that
          its clock was measured.
        </p>
        <dl className="definition">
          <dt>Detected</dt>
          <dd>
            {kindLabel(current.classification.detected_kind).text} (confidence{' '}
            {current.classification.confidence.toFixed(2)})
          </dd>
          <dt>Effective</dt>
          <dd data-testid="effective-kind">
            {kindLabel(current.classification.effective_kind).text}
            {current.classification.is_overridden && ' — your correction'}
          </dd>
        </dl>
        <Sub>What the document showed</Sub>
        <ul className="evidence" data-testid="evidence-list">
          {current.classification.evidence.length === 0 && (
            <li className="muted">Nothing that decides either way.</li>
          )}
          {current.classification.evidence.map((code) => (
            <li key={code}>
              {explainEvidence(code)}
              <span className="muted"> — {leaningLabel(evidenceLeaning(code))}</span>
            </li>
          ))}
        </ul>
        <fieldset className="corrections">
          <legend>Correct the kind</legend>
          <div className="pager">
            <button
              type="button"
              className={current.classification.is_overridden ? '' : 'primary'}
              disabled={!current.classification.is_overridden}
              onClick={() => {
                void correct(null)
              }}
              data-testid="reset-override"
            >
              Use detected
            </button>
            {(['recorded', 'planned', 'unknown'] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                className={current.classification.override === kind ? 'primary' : ''}
                aria-pressed={current.classification.override === kind}
                onClick={() => {
                  void correct(kind)
                }}
                data-testid={`set-${kind}`}
              >
                {kindLabel(kind).text}
              </button>
            ))}
          </div>
        </fieldset>
      </section>
    ),
    source: (
      <section className="panel">
        <Section>Where this track came from</Section>
        <dl className="definition">
          <dt>Format</dt>
          <dd>
            {current.source.exchange_format} {current.source.format_version ?? ''}
          </dd>
          <dt>Created by</dt>
          <dd>{current.source.creator ?? '—'}</dd>
          <dt>Title in the file</dt>
          <dd>{current.metadata.source_title ?? '—'}</dd>
          <dt>Positions</dt>
          <dd>
            {current.point_count} in {current.segment_count} segment
            {current.segment_count === 1 ? '' : 's'}
          </dd>
          <dt>Timeline</dt>
          <dd>
            {formatInstantWithTime(current.timeline.started_at)} –{' '}
            {formatInstantWithTime(current.timeline.ended_at)}
          </dd>
          <dt>Roughly where</dt>
          <dd data-testid="detail-place">
            {current.approximate_location === null
              ? '—'
              : `${current.approximate_location.regions.join(' · ')}${
                  current.approximate_location.countries.length > 0
                    ? ` (${current.approximate_location.countries
                        .map((country) => country.name)
                        .join(', ')})`
                    : ''
                }`}
            {current.approximate_location !== null && (
              <span className="muted">
                {' '}
                — approximate: compared as rectangles, so a track near a border can be named
                for the wrong side.
              </span>
            )}
          </dd>
          <dt>Calendar</dt>
          <dd data-testid="calendar-basis">
            {current.timeline.is_actual_calendar_time
              ? 'Dated by observed timing'
              : 'Not placed in a calendar period'}
          </dd>
        </dl>
      </section>
    ),
    derivation: metrics ? (
      <section className="panel">
        <Section>How the numbers were derived</Section>
        {current.timeline.is_actual_activity_timing && usable && (
          <TimeBreakdown
            moving={metrics.timed_path.moving_duration_s}
            stopped={metrics.timed_path.stopped_duration_s}
            unobserved={metrics.timed_path.unobserved_gap_duration_s}
            unattributed={metrics.timed_path.unattributed_duration_s}
          />
        )}
        {metrics.quality.length > 0 && (
          <>
            <Sub>What was awkward about the data</Sub>
            <ul className="evidence" data-testid="quality-list">
              {metrics.quality.map((flag) => (
                <li key={flag}>{explainQuality(flag)}</li>
              ))}
            </ul>
          </>
        )}
        <details className="technical">
          <summary>Technical detail</summary>
          <dl className="definition">
            <dt>Analysis state</dt>
            <dd>{status.text}</dd>
            <dt>Derived at</dt>
            <dd>{formatInstantWithTime(metrics.analyzed_at)}</dd>
            <dt>Algorithms</dt>
            <dd>
              {metrics.profile
                ? `${metrics.profile.distance_algorithm}/${metrics.profile.distance_algorithm_version}, ${metrics.profile.movement_algorithm}/${metrics.profile.movement_algorithm_version}, ${metrics.profile.elevation_algorithm}/${metrics.profile.elevation_algorithm_version}`
                : '—'}
            </dd>
            <dt>Extension schemas</dt>
            <dd>{current.source.extension_namespaces.join(', ') || 'none'}</dd>
            <dt>Source hash</dt>
            <dd>
              <code>{current.raw_import_sha256.slice(0, 12)}</code>
            </dd>
          </dl>
        </details>
      </section>
    ) : null,
  }
  const available = (widget: string): boolean => {
    if (widget === 'sensors') return sensors
    if (widget === 'derivation') return metrics !== null
    return true
  }

  return (
    <>
      <TitleEditor
        track={current}
        headingLevel={headingLevel}
        onSaved={(updated) => {
          setOverride(updated)
          track.reload()
          onChanged?.()
        }}
      />

      <p className="track-row__meta">
        <Badge label={kindLabel(current.classification.effective_kind)} />
        <span>{current.activity}</span>
        <Badge label={timing} />
        <Badge label={status} />
      </p>

      {!usable && (
        <Notice tone={current.analysis.status === 'invalid' ? 'error' : 'warn'}>
          <span data-testid="analysis-notice">
            {status.hint} The map and the shape below are derived now, from the geometry the archive
            holds, so they are still right; the headline figures are left blank rather than quoted
            from algorithms this build no longer runs.
          </span>
          <details className="operator-hint">
            <summary>What fixes this</summary>
            <p>
              On the machine that holds the data, run <code>trackvault analyze --outdated</code>.
            </p>
          </details>
        </Notice>
      )}

      {!current.timeline.is_actual_activity_timing && (
        <p className="muted" data-testid="timing-caveat">
          {timing.text}. These durations describe the path's own clock and are not verified as time
          somebody spent.
        </p>
      )}

      {layout.settled && (
        <WidgetBoard
          label="Track details"
          catalog={TRACK_WIDGETS}
          defaults={DEFAULT_TRACK_LAYOUT}
          stored={layout.stored}
          widgets={widgets}
          available={available}
          unavailableHint={unavailableHint}
          editable={customizable}
          blocked={layout.blocked}
          notice={
            layout.unreadable ? (
              <Notice>
                <span data-testid="layout-unreadable">
                  The saved layout of this page could not be read, so the default is shown.
                  Arranging the page and saving replaces it.
                </span>
              </Notice>
            ) : null
          }
          onSave={layout.save}
        />
      )}
    </>
  )
}

/** What an empty widget says while the page is being arranged. */
function unavailableHint(widget: string): string {
  if (widget === 'sensors') {
    return 'Shown for tracks where a sensor measured heart rate or cadence. This one has neither.'
  }
  if (widget === 'derivation') return 'Shown once the analysis of this track has been read.'
  return 'Nothing to show here for this track.'
}

/**
 * A track that cannot be shown.
 *
 * A 404 here is ordinary: a bookmark to a track a later reprocess stopped
 * producing, or a link somebody typed. It is a state, not an error page.
 *
 * It carries no way back on purpose. Where a reader can go is a property of the
 * page they are on -- the track's own page has its link to the list, and a row
 * inside that list would be offering to take somebody where they already are.
 */
function TrackUnavailable({
  message,
  onRetry,
  headingLevel,
}: {
  message: string
  onRetry: () => void
  headingLevel: 1 | 2
}) {
  const missing = message.startsWith('Not found')
  const Title = headingLevel === 1 ? 'h1' : 'h2'
  return (
    <div className="panel" role="alert" data-testid="track-unavailable">
      <Title>{missing ? 'No such track' : 'This track could not be loaded'}</Title>
      <p className="muted">{message}</p>
      {!missing && (
        <div className="pager">
          <button type="button" onClick={onRetry} data-testid="retry">
            Try again
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * What a recording's elapsed time actually consisted of.
 *
 * Four numbers that add up to the elapsed time exactly, which is the point: a
 * silence in a recording is `unobserved` rather than a rest, and time no rule
 * could classify is named rather than dropped. One small table, not four cards
 * -- this is context for the headline figures, not a headline of its own.
 */
function TimeBreakdown({
  moving,
  stopped,
  unobserved,
  unattributed,
}: {
  moving: number | null
  stopped: number | null
  unobserved: number | null
  unattributed: number | null
}) {
  const rows: [string, number | null, string][] = [
    ['Moving', moving, 'Positions kept arriving and the track was going somewhere'],
    ['Stopped', stopped, 'Positions kept arriving and showed no movement'],
    ['Not recorded', unobserved, 'Nothing was recorded at all: a pause, a flat battery or a lost fix'],
    ['Undecided', unattributed, 'Observed time no rule could separate from receiver noise'],
  ]
  return (
    <table className="chart-table" data-testid="time-breakdown">
      <caption className="visually-hidden">
        How the elapsed time divides. The four add up to the elapsed duration exactly.
      </caption>
      <thead>
        <tr>
          <th scope="col">Time</th>
          <th scope="col">Duration</th>
          <th scope="col">Meaning</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, seconds, meaning]) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            <td>{formatDuration(seconds)}</td>
            <td className="muted">{meaning}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * The one line that changes as the pointer moves.
 *
 * It subscribes to the hover store on its own, so a mouse move re-renders this
 * paragraph and nothing else. The map moves its marker and the chart moves its
 * cursor through their own APIs.
 */
function HoverReadout({
  store,
  samples,
}: {
  store: ReturnType<typeof useHoverStore>
  samples: ReturnType<typeof flatten>
}) {
  const index = useHovered(store)
  const sample = sampleAt(samples, index)
  return (
    <p className="muted" data-testid="hover-readout" aria-live="off">
      {sample
        ? `${formatDistance(sample.distance_m)} · ${formatElevation(sample.filtered_elevation_m)} · ${formatSpeed(sample.speed_mps)}`
        : 'Point at the chart or the map to read one position.'}
    </p>
  )
}

function leaningLabel(leaning: 'recorded' | 'planned' | 'neither'): string {
  if (leaning === 'recorded') return 'points to a recording'
  if (leaning === 'planned') return 'points to a plan'
  return 'decides neither way'
}

export default TrackReport
