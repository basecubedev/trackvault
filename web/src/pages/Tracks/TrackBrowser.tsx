import { useCallback, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { Activity, AnalysisAvailability, Track, TrackKind, TrackOrder } from '../../api/client'
import { api } from '../../api/client'
import { formatDistance, formatDuration, formatElevation, formatInstant } from '../../api/format'
import { analysisLabel, kindLabel, timingLabel } from '../../api/labels'
import { displayTitle, isFallbackTitle } from '../../api/titles'
import { useRequest } from '../../api/useRequest'
import { ACTIVITIES, ANALYSIS_STATES, ORDERS, SCOPES } from '../../api/vocabulary'
import { Badge } from '../../components/Badge'
import { Notice } from '../../components/Notice'

const PAGE_SIZE = 25
const MONTHS = Array.from({ length: 12 }, (_, index) => index + 1)

/** Every filter this page understands, so resetting is one list rather than six. */
const FILTERS = ['kind', 'activity', 'year', 'month', 'analysis_status'] as const

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

  const page = useRequest((signal) => api.listTracks(query, signal), [JSON.stringify(query)])
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
      setParameters(next)
    },
    [parameters, setParameters],
  )

  const reset = useCallback(() => {
    const next = new URLSearchParams(parameters)
    for (const name of FILTERS) next.delete(name)
    next.delete('offset')
    setParameters(next)
  }, [parameters, setParameters])

  const total = page.data?.total ?? 0
  const shown = page.data?.tracks ?? []
  const years = available.data?.years ?? []
  const archiveIsEmpty = available.data !== null && available.data.archive_track_count === 0

  return (
    <>
      <h1>Tracks</h1>

      <div className="filters">
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
          <TrackRow key={track.id} track={track} />
        ))}
      </ul>

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
          <code>gpx-view import</code> or <code>gpx-view scan</code>.
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

export function TrackRow({ track }: { track: Track }) {
  const analysis = analysisLabel(track.analysis.status)
  const timing = timingLabel(track.timeline.basis)
  const current = track.analysis.status === 'current'
  return (
    <li className="track-row" data-testid={`track-${track.id}`}>
      <span className="track-row__title">
        <Link to={`/tracks/${track.id}`}>{displayTitle(track)}</Link>
        {isFallbackTitle(track) && (
          <span className="muted track-row__untitled"> · untitled in its source</span>
        )}
      </span>
      <span className="track-row__meta">
        <Badge label={kindLabel(track.classification.effective_kind)} />
        <span>{track.activity}</span>
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
      </span>
    </li>
  )
}
