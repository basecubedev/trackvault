import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { Activity, AnalysisAvailability, Track, TrackKind, TrackOrder } from '../../api/client'
import { api } from '../../api/client'
import { formatDistance, formatDuration, formatElevation, formatInstant } from '../../api/format'
import { analysisLabel, kindLabel, timingLabel } from '../../api/labels'
import { displayTitle, isFallbackTitle } from '../../api/titles'
import { useRequest } from '../../api/useRequest'
import { ACTIVITIES, ANALYSIS_STATES, ORDERS, SCOPES } from '../../api/vocabulary'
import { mergeAttribution } from '../../map/style'
import { TrackMinimap } from '../../map/TrackMinimap'
import { Badge } from '../../components/Badge'
import { Notice } from '../../components/Notice'
import { TrackImport } from './TrackImport'

const PAGE_SIZE = 25
const MONTHS = Array.from({ length: 12 }, (_, index) => index + 1)

/** Every filter this page understands, so resetting is one list rather than six. */
const FILTERS = ['kind', 'activity', 'year', 'month', 'analysis_status'] as const

/**
 * The track's own report, fetched when a row is first opened.
 *
 * It brings the map library with it, and most visits to this page never open a
 * row. Loading it up front would make a list of rows the most expensive page in
 * the application.
 */
const TrackReport = lazy(() =>
  import('../TrackDetail/TrackReport').then((module) => ({ default: module.TrackReport })),
)

/**
 * The archive, one page at a time.
 *
 * Every filter is in the URL, so a filtered view is a link somebody can send
 * and a refresh does not lose it. The server owns the page: `total` counts the
 * filtered selection, and this only renders what it is given.
 *
 * The years offered come from the archive rather than from a range around the
 * reader's clock, so every year in the list is one that has something in it.
 */
export function TrackBrowser() {
  const [parameters, setParameters] = useSearchParams()
  const offset = Number(parameters.get('offset') ?? 0)
  const kind = parameters.get('kind') ?? ''
  const activity = parameters.get('activity') ?? ''
  const year = parameters.get('year') ?? ''
  const month = parameters.get('month') ?? ''
  const analysisStatus = parameters.get('analysis_status') ?? ''
  const sort = (parameters.get('sort') ?? 'imported_newest_first') as TrackOrder
  const filtered = FILTERS.some((name) => parameters.get(name))

  const query = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      sort,
      ...(kind ? { kind: kind as TrackKind } : {}),
      ...(activity ? { activity: activity as Activity } : {}),
      ...(year ? { year: Number(year) } : {}),
      ...(year && month ? { month: Number(month) } : {}),
      ...(analysisStatus ? { analysis_status: analysisStatus as AnalysisAvailability } : {}),
    }),
    [offset, sort, kind, activity, year, month, analysisStatus],
  )

  const selection = useMemo(() => JSON.stringify(query), [query])
  const page = useRequest((signal) => api.listTracks(query, signal), [selection])
  // Whether this deployment accepts files at all. Asked rather than assumed:
  // a control the server would refuse is a page that lies about what it does.
  const system = useRequest((signal) => api.readSystemInfo(signal), [])
  const [importing, setImporting] = useState(false)

  // What the previews on this page have to credit. It is learned one drawn map
  // at a time, because which package belongs behind a track is decided per
  // track -- and it is forgotten when the selection changes, since the next
  // page may be somewhere the archive holds no map for at all.
  const [credited, setCredited] = useState<readonly string[]>([])
  useEffect(() => {
    setCredited([])
  }, [selection])
  const credit = useCallback((lines: readonly string[]) => {
    setCredited((seen) => mergeAttribution(seen, lines))
  }, [])

  // Which years exist at all, for the filter and for telling an empty archive
  // apart from an empty selection. One extra request on a page that is already
  // making one, and it answers both questions.
  const available = useRequest(
    (signal) => api.readYears(kind ? { scope: kind } : {}, signal),
    [kind],
  )

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(parameters)
      if (value) next.set(key, value)
      else next.delete(key)
      // A changed filter is a new selection, so paging starts again. Keeping
      // the offset would open page four of a three-page result.
      if (key !== 'offset') next.delete('offset')
      if (key === 'year' && !value) next.delete('month')
      // ...and an opened row belongs to the selection it was opened in. The
      // next one may not hold that track at all.
      next.delete('open')
      setParameters(next)
    },
    [parameters, setParameters],
  )

  const reset = useCallback(() => {
    const next = new URLSearchParams(parameters)
    for (const name of FILTERS) next.delete(name)
    next.delete('offset')
    next.delete('open')
    setParameters(next)
  }, [parameters, setParameters])

  /**
   * Which row is open, in the address.
   *
   * In the address because every other part of this view is: a track somebody
   * found by scanning the list is then a link they can send, and a refresh does
   * not throw the search away. One at a time -- an open report holds a map, and
   * a map holds a WebGL context.
   */
  const opened = Number(parameters.get('open') ?? 0)
  const toggle = useCallback(
    (trackId: number) => {
      const next = new URLSearchParams(parameters)
      if (next.get('open') === String(trackId)) next.delete('open')
      else next.set('open', String(trackId))
      setParameters(next)
    },
    [parameters, setParameters],
  )

  const total = page.data?.total ?? 0
  const shown = page.data?.tracks ?? []
  const years = available.data?.years ?? []
  const archiveIsEmpty = available.data !== null && available.data.archive_track_count === 0

  return (
    <>
      <h1>Tracks</h1>

      <div className="filters">
        <div className="field">
          <span className="visually-hidden" id="import-help">
            Offer files to the archive from this browser
          </span>
          <button
            type="button"
            aria-expanded={importing}
            aria-describedby="import-help"
            data-testid="toggle-import"
            onClick={() => {
              setImporting((open) => !open)
            }}
          >
            {importing ? 'Close import' : 'Import files'}
          </button>
        </div>
        <Select id="kind" label="Kind" value={kind} onChange={update} empty="All kinds">
          {SCOPES.map((scope) => (
            <option key={scope.value} value={scope.value}>
              {scope.label}
            </option>
          ))}
        </Select>
        <Select
          id="activity"
          label="Activity"
          value={activity}
          onChange={update}
          empty="All activities"
        >
          {ACTIVITIES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </Select>
        <Select id="year" label="Year" value={year} onChange={update} empty="Any year">
          {years.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </Select>
        <Select id="month" label="Month" value={month} onChange={update} empty="Any month">
          {MONTHS.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </Select>
        <Select
          id="analysis_status"
          label="Analysis"
          value={analysisStatus}
          onChange={update}
          empty="Any state"
        >
          {ANALYSIS_STATES.map((value) => (
            <option key={value} value={value}>
              {analysisLabel(value).text}
            </option>
          ))}
        </Select>
        <div className="field">
          <label htmlFor="sort">Sort</label>
          <select
            id="sort"
            value={sort}
            onChange={(event) => {
              update('sort', event.target.value)
            }}
          >
            {ORDERS.map((order) => (
              <option key={order.value} value={order.value}>
                {order.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <span className="visually-hidden" id="reset-help">
            Remove every filter and show the whole archive
          </span>
          <button
            type="button"
            onClick={reset}
            disabled={!filtered}
            aria-describedby="reset-help"
            data-testid="reset-filters"
          >
            Reset filters
          </button>
        </div>
      </div>

      {importing && (
        <TrackImport
          enabled={system.data?.upload_enabled ?? false}
          onImported={() => {
            page.reload()
            available.reload()
          }}
        />
      )}

      {page.error !== null && (
        <div role="alert" data-testid="request-error">
          <Notice tone="error">{page.error}</Notice>
          <button type="button" onClick={page.reload} data-testid="retry">
            Try loading the tracks again
          </button>
        </div>
      )}

      <p className="muted" data-testid="result-count">
        {page.loading ? 'Loading…' : `${total} track${total === 1 ? '' : 's'}`}
        {year && ' in the selected period, dated by evidence the archive can vouch for'}
      </p>

      <ul className="track-list">
        {shown.map((track) => (
          <TrackRow
            key={track.id}
            track={track}
            onAttribution={credit}
            expanded={track.id === opened}
            onToggle={toggle}
            onChanged={page.reload}
          />
        ))}
      </ul>

      {credited.length > 0 && (
        <p className="map-attribution" data-testid="list-map-attribution">
          {credited.join(' · ')}
        </p>
      )}

      {shown.length === 0 && !page.loading && page.error === null && (
        <EmptyResult archiveIsEmpty={archiveIsEmpty} filtered={filtered} onReset={reset} />
      )}

      <div className="pager">
        <button
          type="button"
          disabled={offset <= 0}
          onClick={() => {
            update('offset', String(Math.max(0, offset - PAGE_SIZE)))
          }}
        >
          Previous
        </button>
        <span className="muted" data-testid="pager-position">
          {total === 0 ? '0' : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
        </span>
        <button
          type="button"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => {
            update('offset', String(offset + PAGE_SIZE))
          }}
        >
          Next
        </button>
      </div>
    </>
  )
}

/**
 * Nothing to show, and which of the two reasons it is.
 *
 * "There is nothing here" and "there is nothing here *like that*" call for
 * opposite reactions -- import something, or change the filter -- and one
 * message for both sends half the readers the wrong way.
 */
function EmptyResult({
  archiveIsEmpty,
  filtered,
  onReset,
}: {
  archiveIsEmpty: boolean
  filtered: boolean
  onReset: () => void
}) {
  if (archiveIsEmpty) {
    return (
      <div className="panel" data-testid="empty-archive">
        <h2>No tracks imported yet</h2>
        <p className="muted">
          Tracks are imported on the machine that holds the data, with{' '}
          <code>trackvault import</code> or <code>trackvault scan</code>.
        </p>
      </div>
    )
  }
  return (
    <div className="panel" data-testid="empty-filter">
      <h2>No tracks match these filters</h2>
      <p className="muted">The archive holds tracks; none of them match this selection.</p>
      {filtered && (
        <button type="button" onClick={onReset}>
          Reset filters
        </button>
      )}
    </div>
  )
}

function Select({
  id,
  label,
  value,
  empty,
  onChange,
  children,
}: {
  id: string
  label: string
  value: string
  empty: string
  onChange: (key: string, value: string) => void
  children: React.ReactNode
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={value}
        onChange={(event) => {
          onChange(id, event.target.value)
        }}
      >
        <option value="">{empty}</option>
        {children}
      </select>
    </div>
  )
}

/**
 * What the badge on a row of several imports says when you point at it.
 *
 * "Imports", not "duplicates". The archive holds each file because each one is
 * different evidence, and one of them may carry readings the others lost --
 * calling them duplicates invites deleting the wrong one.
 */
function sameRecordingHint(track: Track): string {
  const total = track.same_recording_ids.length + 1
  return (
    `The same recording arrived ${String(total)} times, in ${String(total)} different files. ` +
    'Each is kept: they are different evidence, and one may carry readings the others lost.'
  )
}

/**
 * Roughly where a track was.
 *
 * The tilde is the whole point. What the archive compared is rectangles -- the
 * box around the track against the box around a region's outline -- which is
 * right inside a country and wrong at a border. A label without the mark would
 * read as a fact, and this one cannot be one.
 *
 * The country is shown beside the region, never instead of it: "Limburg" alone
 * is ambiguous, and the pair is what lets a reader notice the wrong one.
 */
export function TrackPlace({ location }: { location: Track['approximate_location'] }) {
  if (location === null || location.regions.length === 0) return null
  const countries = location.countries.map((country) => country.name).join(' / ')
  return (
    <span
      className="track-place"
      data-testid="track-place"
      title="Approximate: the archive compared the rectangle around this track with the rectangle around each region. Near a border it can name the wrong one."
    >
      <span aria-hidden="true">≈ </span>
      {location.regions[0]}
      {countries && <span className="muted"> · {countries}</span>}
    </span>
  )
}

export function TrackRow({
  track,
  onAttribution,
  expanded = false,
  onToggle,
  onChanged,
}: {
  track: Track
  onAttribution?: (lines: readonly string[]) => void
  /** Whether this row is currently showing the track's own report. */
  expanded?: boolean
  onToggle?: (trackId: number) => void
  /** Fired when the report changed something this row is also showing. */
  onChanged?: () => void
}) {
  const analysis = analysisLabel(track.analysis.status)
  const timing = timingLabel(track.timeline.basis)
  const current = track.analysis.status === 'current'
  const title = displayTitle(track)
  const panelId = `track-details-${String(track.id)}`
  return (
    <li className="track-row" data-testid={`track-${track.id}`}>
      <TrackMinimap
        trackId={track.id}
        title={title}
        {...(onAttribution ? { onAttribution } : {})}
      />
      <span className="track-row__title">
        <Link to={`/tracks/${track.id}`}>{displayTitle(track)}</Link>
        {isFallbackTitle(track) && (
          <span className="muted track-row__untitled"> · untitled in its source</span>
        )}
      </span>
      <span className="track-row__meta">
        <Badge label={kindLabel(track.classification.effective_kind)} />
        <span>{track.activity}</span>
        <TrackPlace location={track.approximate_location} />
        <span title={timing.hint}>
          {formatInstant(track.timeline.started_at)}
          {!track.timeline.is_actual_calendar_time && (
            <>
              {' '}
              <span aria-hidden="true">{timing.mark}</span>{' '}
              <span className="muted">unverified date</span>
            </>
          )}
        </span>
        <span className="track-row__metric" data-testid="row-distance">
          {formatDistance(current ? track.analysis.distance_m : null)}
        </span>
        <span className="track-row__metric">
          {formatElevation(current ? track.analysis.elevation_gain_m : null)}
        </span>
        {track.timeline.is_actual_activity_timing && (
          <span className="track-row__metric">
            {formatDuration(current ? track.analysis.moving_duration_s : null)}
          </span>
        )}
        <Badge label={analysis} />
        {track.same_recording_ids.length > 0 && (
          <span data-testid="same-recording">
            <Badge
              label={{
                text: `${String(track.same_recording_ids.length + 1)} imports`,
                tone: 'neutral',
                mark: '⧉',
                hint: sameRecordingHint(track),
              }}
            />
          </span>
        )}
      </span>
      {onToggle && (
        <button
          type="button"
          className="track-row__disclosure"
          data-testid={`expand-${track.id}`}
          aria-expanded={expanded}
          aria-controls={panelId}
          aria-label={`${expanded ? 'Hide' : 'Show'} details for ${title}`}
          onClick={() => {
            onToggle(track.id)
          }}
        >
          <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        </button>
      )}
      {expanded && (
        <div className="track-row__details" id={panelId}>
          <Suspense
            fallback={
              <p className="muted" role="status">
                Loading this track…
              </p>
            }
          >
            <TrackReport
              trackId={track.id}
              headingLevel={2}
              {...(onChanged ? { onChanged } : {})}
            />
          </Suspense>
        </div>
      )}
    </li>
  )
}
