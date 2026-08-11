import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type Activity, type AggregationScope, type AvailableYears } from '../../api/client'
import { formatCount, formatDistance, formatDuration, formatElevation } from '../../api/format'
import { useRequest } from '../../api/useRequest'
import { LazyChart } from '../../charts/LazyChart'
import { Metric } from '../../components/Metric'
import { Notice } from '../../components/Notice'
import { RequestState } from '../../components/RequestState'
import { ACTIVITIES, SCOPES } from '../../api/vocabulary'
import { coverageSummary, coverageWarnings } from './coverage'
import { MONTHLY_METRICS, type MonthlyMetric, monthlyOption, monthlySeries } from './monthly'

/**
 * The first thing somebody sees.
 *
 * The default scope is `recorded`, because the unqualified question is what
 * actually happened. Planned routes and undecided tracks have their own scopes
 * and are never added to it -- a total that mixed them would be about neither,
 * and the archive refuses to compute one anyway.
 *
 * The year is not the reader's current year either. It is the most recent year
 * the *archive* has something to show for, which the archive itself answers: an
 * interface that generated a range from the machine's clock opened an archive of
 * 2019 recordings on an empty 2026 and looked broken.
 */
export function Dashboard() {
  const [parameters, setParameters] = useSearchParams()
  const scope = (parameters.get('scope') ?? 'recorded') as AggregationScope
  const activity = (parameters.get('activity') ?? '') as Activity | ''

  const available = useRequest(
    (signal) => api.readYears({ scope, ...(activity ? { activity } : {}) }, signal),
    [scope, activity],
  )

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(parameters)
      if (value) next.set(key, value)
      else next.delete(key)
      // A different scope has different years, and the year in the address bar
      // may not be one of them. Dropping it lets the archive choose again.
      if (key !== 'year') next.delete('year')
      setParameters(next, { replace: true })
    },
    [parameters, setParameters],
  )

  return (
    <>
      <h1>Dashboard</h1>

      <div className="filters">
        <div className="field">
          <label htmlFor="scope">Scope</label>
          <select
            id="scope"
            value={scope}
            onChange={(event) => {
              update('scope', event.target.value)
            }}
          >
            {SCOPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="activity">Activity</label>
          <select
            id="activity"
            value={activity}
            onChange={(event) => {
              update('activity', event.target.value)
            }}
          >
            <option value="">All activities</option>
            {ACTIVITIES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="muted" data-testid="scope-explainer">
        {SCOPES.find((option) => option.value === scope)?.hint}
      </p>

      <RequestState request={available} label="this archive's years">
        {(years) => (
          <Period
            scope={scope}
            activity={activity}
            years={years.years}
            unplaced={years.unplaced}
            archiveTrackCount={years.archive_track_count}
            selected={selectedYear(parameters.get('year'), years.years)}
            onSelectYear={(value) => {
              update('year', value)
            }}
          />
        )}
      </RequestState>
    </>
  )
}

/**
 * Which year to show: the one asked for, if the archive has it.
 *
 * A year in the address bar that the archive has nothing for is a stale link or
 * a scope change, and honouring it would show an empty page that looks like a
 * defect. The newest available year is the answer instead, and `null` means the
 * archive has no dated tracks in this scope at all.
 */
export function selectedYear(requested: string | null, years: readonly number[]): number | null {
  const asked = requested === null ? null : Number(requested)
  if (asked !== null && !Number.isNaN(asked) && years.includes(asked)) return asked
  return years[0] ?? null
}

function Period({
  scope,
  activity,
  years,
  unplaced,
  archiveTrackCount,
  selected,
  onSelectYear,
}: {
  scope: AggregationScope
  activity: Activity | ''
  years: readonly number[]
  unplaced: AvailableYears['unplaced']
  archiveTrackCount: number
  selected: number | null
  onSelectYear: (year: string) => void
}) {
  const navigate = useNavigate()
  const [metric, setMetric] = useState<MonthlyMetric>('distance')
  const query = useMemo(
    () => ({ scope, ...(activity ? { activity } : {}) }),
    [scope, activity],
  )

  const yearly = useRequest(
    (signal) => (selected === null ? Promise.resolve(null) : api.readYear(selected, query, signal)),
    [selected, scope, activity],
  )
  const monthly = useRequest(
    (signal) =>
      selected === null ? Promise.resolve(null) : api.readMonthly(selected, query, signal),
    [selected, scope, activity],
  )

  const points = useMemo(
    () => (monthly.data ? monthlySeries(monthly.data, metric) : []),
    [monthly.data, metric],
  )
  const option = useMemo(() => monthlyOption(points, metric), [points, metric])

  const openMonth = useCallback(
    (month: number) => {
      // The backend owns the period. The interface hands over the same filters
      // the chart was drawn with and never recomputes a month boundary.
      const next = new URLSearchParams({ year: String(selected), month: String(month) })
      if (activity) next.set('activity', activity)
      next.set('kind', scope)
      void navigate(`/tracks?${next.toString()}`)
    },
    [navigate, selected, scope, activity],
  )

  if (archiveTrackCount === 0) return <EmptyArchive />
  if (selected === null) return <NothingDated scope={scope} unplaced={unplaced} />

  const totals = yearly.data?.totals
  const unplacedTotals = yearly.data?.unplaced
  const warnings = totals ? coverageWarnings(totals) : []
  const coverage = totals ? coverageSummary(totals) : null
  const timed = scope === 'recorded'

  return (
    <>
      <div className="filters">
        <div className="field">
          <label htmlFor="year">Year</label>
          <select
            id="year"
            value={selected}
            onChange={(event) => {
              onSelectYear(event.target.value)
            }}
          >
            {years.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
      </div>

      {yearly.error !== null && <Notice tone="error">{yearly.error}</Notice>}

      <div className="cards">
        <Metric
          label="Distance"
          testId="metric-distance"
          value={formatDistance(totals?.distance_m)}
          {...(coverage ? { note: coverage } : {})}
        />
        <Metric label="Track count" testId="metric-tracks" value={formatCount(totals?.track_count)} />
        {/*
          Moving time is only offered where it can mean what it says. A route
          somebody drew has a derived duration, and calling it "moving time"
          beside a distance would present a schedule as an afternoon.
        */}
        {timed ? (
          <Metric
            label="Moving time"
            testId="metric-moving"
            value={formatDuration(totals?.moving_duration_s)}
            {...(totals && totals.tracks_without_observed_timing > 0
              ? { note: `${totals.tracks_without_observed_timing} without observed timing` }
              : {})}
          />
        ) : (
          <Metric
            label="Moving time"
            testId="metric-moving"
            value="—"
            note="Only recorded tracks have time somebody spent"
          />
        )}
        <Metric
          label="Elevation gain"
          testId="metric-elevation"
          value={formatElevation(totals?.elevation_gain_m)}
        />
      </div>

      {warnings.length > 0 && (
        <Notice>
          <span data-testid="coverage-warning">
            {warnings.map((warning, index) => (
              <span key={warning.key}>
                {index > 0 && ' · '}
                {warning.state === null ? (
                  warning.text
                ) : (
                  // Straight to the tracks the counter counted, filtered by the
                  // same availability state the total left out.
                  <Link
                    to={`/tracks?kind=${scope}&analysis_status=${warning.state}${
                      activity ? `&activity=${activity}` : ''
                    }`}
                  >
                    {warning.text}
                  </Link>
                )}
              </span>
            ))}
            . The archive leaves them out of the totals rather than mixing two algorithm
            generations into one number.
          </span>
          <details className="operator-hint">
            <summary>What fixes this</summary>
            <p>
              On the machine that holds the data, run <code>gpx-view analyze --outdated</code>.
            </p>
          </details>
        </Notice>
      )}

      {unplacedTotals !== undefined &&
        unplacedTotals.without_date.track_count +
          unplacedTotals.with_unverified_date.track_count >
          0 && (
          <Notice>
            <span data-testid="unplaced-note">
              {unplacedTotals.without_date.track_count} track(s) carry no date and{' '}
              {unplacedTotals.with_unverified_date.track_count} carry a date nothing vouches for.
              Neither belongs to a month, so neither is in the totals above.
            </span>
          </Notice>
        )}

      <h2 id="monthly-heading">Monthly {metricLabel(metric)}</h2>
      <div className="filters">
        <div className="field">
          <label htmlFor="metric">Show</label>
          <select
            id="metric"
            value={metric}
            onChange={(event) => {
              setMetric(event.target.value as MonthlyMetric)
            }}
          >
            {MONTHLY_METRICS.filter((definition) => timed || definition.key !== 'moving').map(
              (definition) => (
                <option key={definition.key} value={definition.key}>
                  {definition.label}
                </option>
              ),
            )}
          </select>
        </div>
      </div>

      <div className="panel">
        {monthly.data === null ? (
          <p className="chart-fallback">{monthly.error ?? 'Loading…'}</p>
        ) : (
          <LazyChart option={option} label={`Monthly ${metricLabel(metric)} for ${selected}`} />
        )}

        <table className="chart-table" data-testid="monthly-table">
          <caption className="visually-hidden">
            Monthly {metricLabel(metric)} for {selected}. Select a month to list its tracks.
          </caption>
          <thead>
            <tr>
              <th scope="col">Month</th>
              <th scope="col">{metricLabel(metric)}</th>
              <th scope="col">Tracks</th>
              <th scope="col">Current</th>
              <th scope="col">
                <span className="visually-hidden">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.month}>
                <th scope="row">{point.label}</th>
                <td>{point.value === null ? '—' : point.value.toFixed(1)}</td>
                <td>{point.trackCount}</td>
                <td>{point.analysedTrackCount}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => {
                      openMonth(point.month)
                    }}
                    data-testid={`open-month-${point.month}`}
                  >
                    Open
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

/**
 * What a fresh installation says.
 *
 * Deliberately not "upload your first GPX": there is no upload endpoint, and
 * telling somebody to use one is worse than telling them nothing. Importing is
 * an operator action on the machine that holds the data, and that is what this
 * says.
 */
function EmptyArchive() {
  return (
    <div className="panel" data-testid="empty-archive">
      <h2>No tracks yet</h2>
      <p className="muted">
        This archive is empty. Tracks are imported on the machine that holds the data:
      </p>
      <pre>
        <code>
          gpx-view import /path/to/track.gpx{'\n'}
          gpx-view scan
        </code>
      </pre>
      <p className="muted">
        <code>scan</code> reads the directory <code>GPX_VIEW_IMPORT_DIR</code> points at, and never
        writes to it.
      </p>
    </div>
  )
}

/**
 * The archive holds tracks, but none this scope can place in a calendar year.
 *
 * The unplaced tracks are named here rather than left out, and the two halves
 * are kept apart: no instants at all and instants nothing vouches for are
 * different facts, and only the second one looks like a date until somebody
 * checks it.
 */
function NothingDated({
  scope,
  unplaced,
}: {
  scope: AggregationScope
  unplaced: AvailableYears['unplaced']
}) {
  const total = unplaced.without_date + unplaced.with_unverified_date
  return (
    <div className="panel" data-testid="nothing-dated">
      <h2>Nothing dated in this scope</h2>
      <p className="muted">
        The archive holds no {scope} track whose activity date it can vouch for, so there is no
        year to total.
      </p>
      {total > 0 && (
        <p className="muted" data-testid="unplaced-note">
          {unplaced.without_date} track(s) carry no date and {unplaced.with_unverified_date} carry a
          date nothing vouches for. Both have a length and belong to no period.
        </p>
      )}
      <p>
        <Link to={`/tracks?kind=${scope}`}>List every {scope} track</Link>
      </p>
    </div>
  )
}

function metricLabel(metric: MonthlyMetric): string {
  return MONTHLY_METRICS.find((definition) => definition.key === metric)?.label ?? metric
}
