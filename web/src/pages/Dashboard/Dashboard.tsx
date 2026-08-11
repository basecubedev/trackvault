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
import { usePrefersDark } from '../../charts/theme'
import {
  activityBars,
  monthBuckets,
  MONTHLY_METRICS,
  type MonthlyMetric,
  monthlyOption,
  yearBuckets,
} from './monthly'

/** Nothing to draw yet. One object, so a render with no data is not a new one. */
const EMPTY_CHART = { labels: [], points: [], bars: [] }

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
 * What the dashboard is showing: one year, every year, or nothing.
 *
 * `'all'` is not a year and is deliberately not modelled as one. It selects a
 * different question -- what the archive holds rather than what a period did --
 * and the archive answers it with years inside it rather than months.
 */
export type PeriodSelection = number | 'all' | null

/**
 * Which period to show: the one asked for, if the archive has it.
 *
 * A year in the address bar that the archive has nothing for is a stale link or
 * a scope change, and honouring it would show an empty page that looks like a
 * defect. The newest available year is the answer instead, and `null` means the
 * archive has no dated tracks in this scope at all -- in which case `all` is
 * refused too, because there is nothing for it to be all of.
 */
export function selectedYear(requested: string | null, years: readonly number[]): PeriodSelection {
  if (years.length === 0) return null
  if (requested === ALL_YEARS) return ALL_YEARS
  const asked = requested === null ? null : Number(requested)
  if (asked !== null && !Number.isNaN(asked) && years.includes(asked)) return asked
  return years[0] ?? null
}

export const ALL_YEARS = 'all'

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
  selected: PeriodSelection
  onSelectYear: (year: string) => void
}) {
  const navigate = useNavigate()
  const [metric, setMetric] = useState<MonthlyMetric>('distance')
  const query = useMemo(
    () => ({ scope, ...(activity ? { activity } : {}) }),
    [scope, activity],
  )

  // One year is two questions -- what it added up to, and what its months did.
  // Every year is one: the archive answers the total and the years inside it
  // together, because the buckets *are* the answer rather than a breakdown of
  // it.
  const everything = selected === ALL_YEARS
  const year = typeof selected === 'number' ? selected : null

  const yearly = useRequest(
    (signal) => (year === null ? Promise.resolve(null) : api.readYear(year, query, signal)),
    [year, scope, activity],
  )
  const monthly = useRequest(
    (signal) => (year === null ? Promise.resolve(null) : api.readMonthly(year, query, signal)),
    [year, scope, activity],
  )
  const overall = useRequest(
    (signal) => (everything ? api.readOverall(query, signal) : Promise.resolve(null)),
    [everything, scope, activity],
  )

  // The palette is handed to the chart rather than inherited: a canvas is
  // outside the stylesheet's reach, and the two modes are separately chosen.
  const dark = usePrefersDark()
  const chart = useMemo(() => {
    if (everything) {
      return overall.data
        ? activityBars(yearBuckets(overall.data), overall.data.activities, metric)
        : EMPTY_CHART
    }
    return monthly.data
      ? activityBars(monthBuckets(monthly.data), monthly.data.activities, metric)
      : EMPTY_CHART
  }, [everything, overall.data, monthly.data, metric])
  const points = chart.points
  // A column per activity only where there is more than one: with a single
  // activity the column and the total would be the same number twice.
  const split = chart.bars.length > 1 ? chart.bars : []
  const option = useMemo(() => monthlyOption(chart, metric, { dark }), [chart, metric, dark])

  const openPeriod = useCallback(
    (key: number) => {
      // The backend owns the period. The interface hands over the same filters
      // the chart was drawn with and never recomputes a boundary. A bucket of
      // the whole archive is a year; a bucket of a year is a month.
      const next = everything
        ? new URLSearchParams({ year: String(key) })
        : new URLSearchParams({ year: String(year), month: String(key) })
      if (activity) next.set('activity', activity)
      next.set('kind', scope)
      void navigate(`/tracks?${next.toString()}`)
    },
    [navigate, everything, year, scope, activity],
  )

  if (archiveTrackCount === 0) return <EmptyArchive />
  if (selected === null) return <NothingDated scope={scope} unplaced={unplaced} />

  const period = everything ? overall : monthly
  const totals = everything ? overall.data?.totals : yearly.data?.totals
  const unplacedTotals = everything ? overall.data?.unplaced : yearly.data?.unplaced
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
            {/*
              Every year at once, offered beside the years rather than instead
              of them: "what have I done" and "what did I do in 2025" are two
              questions, and the archive answers them with different shapes.
            */}
            <option value={ALL_YEARS}>All years</option>
            {years.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
      </div>

      {(everything ? overall.error : yearly.error) !== null && (
        <Notice tone="error">{everything ? overall.error : yearly.error}</Notice>
      )}

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

      <h2 id="monthly-heading">
        {everything ? 'Yearly' : 'Monthly'} {metricLabel(metric)}
      </h2>
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
        {period.data === null ? (
          <p className="chart-fallback">{period.error ?? 'Loading…'}</p>
        ) : (
          <LazyChart
            option={option}
            label={
              everything
                ? `${metricLabel(metric)} per year`
                : `Monthly ${metricLabel(metric)} for ${String(selected)}`
            }
          />
        )}

        {/*
          The same numbers the chart draws, as text. Not a courtesy: three of
          the eight series colours sit below 3:1 against a white surface, and a
          readable table is the relief that permits them. It also carries the
          per-activity split for anyone the colours do not reach at all.
        */}
        <table className="chart-table" data-testid="monthly-table">
          <caption className="visually-hidden">
            {everything
              ? `${metricLabel(metric)} per year`
              : `Monthly ${metricLabel(metric)} for ${String(selected)}`}
            {split.length > 0 && ', per activity'}. Select a {everything ? 'year' : 'month'} to list
            its tracks.
          </caption>
          <thead>
            <tr>
              <th scope="col">{everything ? 'Year' : 'Month'}</th>
              {split.map((bar) => (
                <th scope="col" key={bar.activity}>
                  {bar.activity}
                </th>
              ))}
              <th scope="col">{split.length > 0 ? 'All' : metricLabel(metric)}</th>
              <th scope="col">Tracks</th>
              <th scope="col">Current</th>
              <th scope="col">
                <span className="visually-hidden">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {points.map((point, index) => (
              <tr key={point.key}>
                <th scope="row">{point.label}</th>
                {split.map((bar) => (
                  <td key={bar.activity}>{formatCell(bar.values[index])}</td>
                ))}
                <td>{formatCell(point.value)}</td>
                <td data-testid={`month-${point.key}-tracks`}>{point.trackCount}</td>
                <td>{point.analysedTrackCount}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => {
                      openPeriod(point.key)
                    }}
                    data-testid={`open-month-${point.key}`}
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
 * It used to say only "importing is an operator action", because there was no
 * upload endpoint and pointing at one that did not exist would have been worse
 * than saying nothing. There is one now, so the first thing offered is the one
 * that works from here -- and the operator commands stay, because they are what
 * a phone sync folder and a hundred files at once actually use.
 */
function EmptyArchive() {
  return (
    <div className="panel" data-testid="empty-archive">
      <h2>No tracks yet</h2>
      <p className="muted">
        This archive is empty. <Link to="/tracks">Import files</Link> from here, or import them on
        the machine that holds the data:
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

/** One cell of the monthly table. An absent value is a dash, never a zero. */
function formatCell(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(1)
}

function metricLabel(metric: MonthlyMetric): string {
  return MONTHLY_METRICS.find((definition) => definition.key === metric)?.label ?? metric
}
