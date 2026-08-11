import { describe, expect, it } from 'vitest'
import type { MonthlyStatistics, Totals } from '../../api/client'
import type { Activity } from '../../api/client'
import type { EChartsOption } from '../../charts/echarts'
import { coverageSummary, coverageWarnings } from './coverage'
import { activityBars, monthBuckets, monthlyOption, periodSeries, yearBuckets } from './monthly'

function totals(overrides: Partial<Totals> = {}): Totals {
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

function statistics(months: Partial<Totals>[]): MonthlyStatistics {
  return {
    year: 2025,
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    months: months.map((overrides, index) => ({
      month: index + 1,
      totals: totals(overrides),
      by_activity: [],
    })),
    activities: [],
  }
}

/** A year whose months are given as `{activity: totals}`, the way the archive answers. */
function byActivity(
  months: Partial<Record<Activity, Partial<Totals>>>[],
  activities: Activity[],
): MonthlyStatistics {
  return {
    year: 2025,
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    activities,
    months: months.map((split, index) => {
      const entries = activities.filter((activity) => split[activity] !== undefined)
      return {
        month: index + 1,
        totals: totals({
          track_count: entries.reduce(
            (sum, activity) => sum + (split[activity]?.track_count ?? 0),
            0,
          ),
          distance_m: entries.reduce((sum, activity) => sum + (split[activity]?.distance_m ?? 0), 0),
        }),
        by_activity: entries.map((activity) => ({
          activity,
          totals: totals(split[activity]),
        })),
      }
    }),
  }
}

const EMPTY_YEAR = Array.from({ length: 12 }, () => ({}))

describe('the monthly series', () => {
  it('always has twelve months, including the empty ones', () => {
    const series = periodSeries(
      monthBuckets(statistics(Array.from({ length: 12 }, () => ({})))),
      'distance',
    )

    expect(series).toHaveLength(12)
    expect(series.map((point) => point.label)[0]).toBe('Jan')
    expect(series.map((point) => point.label)[11]).toBe('Dec')
  })

  it('keeps an unavailable metric absent rather than plotting it as zero', () => {
    const series = periodSeries(
      monthBuckets(
        statistics([{ track_count: 3, distance_m: null }, ...Array.from({ length: 11 }, () => ({}))]),
      ),
      'distance',
    )

    expect(series[0]?.value).toBeNull()
    expect(series[0]?.trackCount).toBe(3)
  })

  it('converts metres to kilometres for the axis and nowhere else', () => {
    const series = periodSeries(
      monthBuckets(
        statistics([{ distance_m: 9270.7 }, ...Array.from({ length: 11 }, () => ({}))]),
      ),
      'distance',
    )

    expect(series[0]?.value).toBeCloseTo(9.2707, 4)
  })

  it('carries the coverage a tooltip needs', () => {
    const series = periodSeries(
      monthBuckets(
        statistics([
          { track_count: 5, analysed_track_count: 3 },
          ...Array.from({ length: 11 }, () => ({})),
        ]),
      ),
      'tracks',
    )

    expect(series[0]?.trackCount).toBe(5)
    expect(series[0]?.analysedTrackCount).toBe(3)
  })
})

describe('coverage', () => {
  it('says nothing when a period is complete', () => {
    expect(coverageSummary(totals({ track_count: 4, analysed_track_count: 4 }))).toBeNull()
    expect(coverageWarnings(totals({ track_count: 4, analysed_track_count: 4 }))).toEqual([])
  })

  it('says how much of a period a total covers when it is partial', () => {
    expect(coverageSummary(totals({ track_count: 49, analysed_track_count: 47 }))).toBe(
      '47 of 49 tracks current',
    )
  })

  it('separates the shortfalls, because they need different actions', () => {
    const warnings = coverageWarnings(
      totals({
        track_count: 4,
        analysed_track_count: 1,
        tracks_with_outdated_analysis: 2,
        tracks_with_invalid_analysis: 1,
      }),
    )

    expect(warnings.map((warning) => warning.key)).toEqual(['outdated', 'invalid'])
    expect(warnings[0]?.text).toBe('2 tracks need re-analysis')
    expect(warnings[1]?.text).toBe('1 track has invalid derived data')
  })
})

/**
 * One bar per activity per month.
 *
 * A month's total answers "how far"; a reader looking at a year usually wants
 * "how far doing what". Everything the chart decides about that is here, as
 * plain functions -- which activities get a bar, in what order, in what colour,
 * and what happens to a month an activity was not out in.
 */
describe('the bars one month shows', () => {
  it('gives every activity the year holds its own bar', () => {
    const year = byActivity(
      [{ walking: { distance_m: 4000 }, cycling: { distance_m: 20_000 } }, ...EMPTY_YEAR.slice(1)],
      ['walking', 'cycling'],
    )
    const chart = activityBars(monthBuckets(year), year.activities, 'distance')

    expect(chart.bars.map((bar) => bar.activity)).toEqual(['walking', 'cycling'])
    expect(chart.bars[0]?.values[0]).toBe(4)
    expect(chart.bars[1]?.values[0]).toBe(20)
  })

  it('leaves a month an activity was not out in empty rather than drawing a zero', () => {
    const year = byActivity(
      [{ cycling: { distance_m: 20_000 } }, { walking: { distance_m: 4000 } }, ...EMPTY_YEAR.slice(2)],
      ['walking', 'cycling'],
    )
    const chart = activityBars(monthBuckets(year), year.activities, 'distance')

    // No walk in January is not a January of no kilometres walked -- it is a
    // month this activity was not in, and a zero-height bar claims otherwise.
    expect(chart.bars[0]?.values[0]).toBeNull()
    expect(chart.bars[1]?.values[1]).toBeNull()
  })

  it('keeps twelve slots per activity, so the axis does not move', () => {
    const year = byActivity([{ walking: { distance_m: 4000 } }, ...EMPTY_YEAR.slice(1)], ['walking'])
    const chart = activityBars(monthBuckets(year), year.activities, 'distance')

    expect(chart.labels).toHaveLength(12)
    expect(chart.bars[0]?.values).toHaveLength(12)
  })

  it('draws nothing per activity for a year that holds none', () => {
    const chart = activityBars(monthBuckets(statistics(EMPTY_YEAR)), [], 'distance')

    expect(chart.bars).toEqual([])
    expect(chart.labels).toHaveLength(12)
  })
})

describe('the monthly chart', () => {
  const twoActivities = byActivity(
    [{ walking: { distance_m: 4000 }, cycling: { distance_m: 20_000 } }, ...EMPTY_YEAR.slice(1)],
    ['walking', 'cycling'],
  )

  it('names every activity in a legend, so identity is never colour alone', () => {
    const option = monthlyOption(activityBars(monthBuckets(twoActivities), twoActivities.activities, 'distance'), 'distance', {
      dark: false,
    })

    expect(legendOf(option)?.data).toEqual(['walking', 'cycling'])
  })

  it('gives an activity the same colour whatever else the year holds', () => {
    const alone = byActivity([{ cycling: { distance_m: 20_000 } }, ...EMPTY_YEAR.slice(1)], [
      'cycling',
    ])

    const together = monthlyOption(activityBars(monthBuckets(twoActivities), twoActivities.activities, 'distance'), 'distance', {
      dark: false,
    })
    const filtered = monthlyOption(activityBars(monthBuckets(alone), alone.activities, 'distance'), 'distance', { dark: false })

    // Colour follows the activity, not its position in this particular year:
    // filtering a chart must not repaint the series that survive the filter.
    expect(colorOf(filtered, 0)).toBe(colorOf(together, 1))
  })

  it('is drawn in steps chosen for a dark surface rather than the light ones', () => {
    const light = monthlyOption(activityBars(monthBuckets(twoActivities), twoActivities.activities, 'distance'), 'distance', {
      dark: false,
    })
    const dark = monthlyOption(activityBars(monthBuckets(twoActivities), twoActivities.activities, 'distance'), 'distance', { dark: true })

    expect(colorOf(dark, 0)).not.toBe(colorOf(light, 0))
  })

  it('offers no legend for a single activity, because the heading already names it', () => {
    const alone = byActivity([{ cycling: { distance_m: 20_000 } }, ...EMPTY_YEAR.slice(1)], [
      'cycling',
    ])

    const option = monthlyOption(activityBars(monthBuckets(alone), alone.activities, 'distance'), 'distance', { dark: false })

    expect(legendOf(option)).toBeUndefined()
  })

  it('falls back to one bar per month for a year with no activity breakdown', () => {
    const option = monthlyOption(activityBars(monthBuckets(statistics(EMPTY_YEAR)), [], 'distance'), 'distance', {
      dark: false,
    })

    expect(seriesOf(option)).toHaveLength(1)
    expect(legendOf(option)).toBeUndefined()
  })
})

/**
 * The option is a loose ECharts record, so these read it through one narrow
 * cast each rather than sprinkling assertions through the tests.
 */
function seriesOf(option: EChartsOption): { itemStyle?: { color?: string } }[] {
  return (option as { series?: { itemStyle?: { color?: string } }[] }).series ?? []
}

function legendOf(option: EChartsOption): { data?: string[] } | undefined {
  return (option as { legend?: { data?: string[] } }).legend
}

function colorOf(option: EChartsOption, index: number): string | undefined {
  return seriesOf(option)[index]?.itemStyle?.color
}

/**
 * The whole archive, one bucket per year.
 *
 * The same chart machinery as a year's months -- what changes is what a bucket
 * is, and nothing else. Which is the point: a second builder for "years" would
 * be a second place for a bar to mean something different.
 */
describe('the buckets of every year', () => {
  const archive = {
    scope: 'recorded' as const,
    activity: null,
    timezone: 'UTC',
    totals: totals({ track_count: 3, distance_m: 30_000 }),
    unplaced: { without_date: totals(), with_unverified_date: totals() },
    activities: ['walking', 'cycling'] as Activity[],
    years: [
      {
        year: 2024,
        totals: totals({ track_count: 1, distance_m: 10_000 }),
        by_activity: [
          { activity: 'walking' as const, totals: totals({ track_count: 1, distance_m: 10_000 }) },
        ],
      },
      {
        year: 2025,
        totals: totals({ track_count: 2, distance_m: 20_000 }),
        by_activity: [
          { activity: 'cycling' as const, totals: totals({ track_count: 2, distance_m: 20_000 }) },
        ],
      },
    ],
  }

  it('labels a bucket with its year', () => {
    expect(yearBuckets(archive).map((bucket) => bucket.label)).toEqual(['2024', '2025'])
  })

  it('holds only the years the archive has something for', () => {
    // Twelve months are always twelve, because a calendar has twelve. Years are
    // however many there are: an empty one is a fact about nothing.
    expect(yearBuckets(archive)).toHaveLength(2)
  })

  it('draws one bar per activity there too, with the gaps left empty', () => {
    const chart = activityBars(yearBuckets(archive), archive.activities, 'distance')

    expect(chart.bars.map((bar) => bar.activity)).toEqual(['walking', 'cycling'])
    expect(chart.bars[0]?.values).toEqual([10, null])
    expect(chart.bars[1]?.values).toEqual([null, 20])
  })
})
