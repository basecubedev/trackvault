import { shortMonthName } from '../../api/format'
import type { Activity, MonthlyStatistics, OverallStatistics, Totals } from '../../api/client'
import type { EChartsOption } from '../../charts/echarts'
import { activityColor, seriesColor } from '../../charts/palette'

/**
 * What the monthly chart draws, as a plain function.
 *
 * Kept out of the component so the interesting part -- which months are empty,
 * which values are absent, what a tooltip says -- is checkable without a
 * canvas.
 */

export type MonthlyMetric = 'distance' | 'tracks' | 'elevation' | 'moving'

export interface MetricDefinition {
  readonly key: MonthlyMetric
  readonly label: string
  readonly axis: string
  /** Reads the value out of a month, already converted for display. */
  readonly read: (totals: MonthlyStatistics['months'][number]['totals']) => number | null
}

export const MONTHLY_METRICS: readonly MetricDefinition[] = [
  {
    key: 'distance',
    label: 'Distance',
    axis: 'km',
    read: (totals) => (totals.distance_m === null ? null : totals.distance_m / 1000),
  },
  { key: 'tracks', label: 'Track count', axis: 'tracks', read: (totals) => totals.track_count },
  {
    key: 'elevation',
    label: 'Elevation gain',
    axis: 'm',
    read: (totals) => totals.elevation_gain_m,
  },
  {
    key: 'moving',
    label: 'Moving time',
    axis: 'h',
    read: (totals) =>
      totals.moving_duration_s === null ? null : totals.moving_duration_s / 3600,
  },
]

const DEFAULT_METRIC: MetricDefinition = {
  key: 'distance',
  label: 'Distance',
  axis: 'km',
  read: (totals) => (totals.distance_m === null ? null : totals.distance_m / 1000),
}

export function metricDefinition(key: MonthlyMetric): MetricDefinition {
  return MONTHLY_METRICS.find((metric) => metric.key === key) ?? DEFAULT_METRIC
}

export interface MonthlyPoint {
  /** The month of a year, or the year itself. What "Open" opens. */
  readonly key: number
  readonly label: string
  readonly value: number | null
  readonly trackCount: number
  readonly analysedTrackCount: number
}

/**
 * One bucket of whatever period the reader picked.
 *
 * A year's buckets are its twelve months; the whole archive's are its years.
 * Everything below this line is written against buckets rather than months,
 * because the two views differ in what a bucket *is* and in nothing else.
 */
export interface PeriodBucket {
  readonly key: number
  readonly label: string
  readonly totals: Totals
  readonly byActivity: readonly { activity: Activity; totals: Totals }[]
}

/** Twelve buckets: the months of one year, empty ones included. */
export function monthBuckets(statistics: MonthlyStatistics): PeriodBucket[] {
  return statistics.months.map((bucket) => ({
    key: bucket.month,
    label: shortMonthName(bucket.month),
    totals: bucket.totals,
    byActivity: bucket.by_activity,
  }))
}

/**
 * One bucket per year the archive holds something for.
 *
 * Not padded out to a fixed span the way months are. An empty month is a fact
 * about its year; an empty year is a fact about nothing, and a chart that drew
 * one per year of the supported calendar would be a century of blank bars.
 */
export function yearBuckets(statistics: OverallStatistics): PeriodBucket[] {
  return statistics.years.map((bucket) => ({
    key: bucket.year,
    label: String(bucket.year),
    totals: bucket.totals,
    byActivity: bucket.by_activity,
  }))
}

/**
 * One point per bucket, in the order the archive gave them.
 *
 * A month with nothing in it is present and empty rather than missing: a chart
 * that drops empty months redraws its own axis every time somebody imports a
 * file, and a year with one ride in July then looks like a year with one month.
 */
export function periodSeries(
  buckets: readonly PeriodBucket[],
  metric: MonthlyMetric,
): MonthlyPoint[] {
  const read = metricDefinition(metric).read
  return buckets.map((bucket) => ({
    key: bucket.key,
    label: bucket.label,
    value: read(bucket.totals),
    trackCount: bucket.totals.track_count,
    analysedTrackCount: bucket.totals.analysed_track_count,
  }))
}

export interface MonthlyBar {
  readonly activity: Activity
  /** Twelve slots in calendar order; `null` where this activity was not out. */
  readonly values: (number | null)[]
}

export interface MonthlyChart {
  readonly labels: string[]
  /** The month totals, which the tooltip and the table read. */
  readonly points: MonthlyPoint[]
  /** One entry per activity the year holds, in the order the archive named them. */
  readonly bars: MonthlyBar[]
}

/**
 * A period as one bar per activity per bucket.
 *
 * The order is the archive's, which is the taxonomy's -- never this year's
 * ranking. An activity keeps its place, and therefore its colour, whatever else
 * the archive holds, so filtering the chart cannot repaint the series that
 * survive the filter.
 *
 * A month an activity was not out in is `null` rather than `0`. Zero is a claim
 * that somebody went nowhere; absence says they were not there at all, and a
 * zero-height bar in a row of twelve reads as the first.
 */
export function activityBars(
  buckets: readonly PeriodBucket[],
  activities: readonly Activity[],
  metric: MonthlyMetric,
): MonthlyChart {
  const read = metricDefinition(metric).read
  const points = periodSeries(buckets, metric)
  return {
    labels: points.map((point) => point.label),
    points,
    bars: activities.map((activity) => ({
      activity,
      values: buckets.map((bucket) => {
        const entry = bucket.byActivity.find((split) => split.activity === activity)
        return entry === undefined ? null : read(entry.totals)
      }),
    })),
  }
}

/** How wide one bar may get, however few buckets there are to share the width. */
const BAR_MAX_WIDTH = 44

/** The ink the axes and the legend are written in. Text never wears a series colour. */
const INK = { light: '#5c636b', dark: '#a4acb4' }
const RULE = { light: '#d7dbe0', dark: '#333a41' }

export function monthlyOption(
  chart: MonthlyChart,
  metric: MonthlyMetric,
  { dark }: { dark: boolean },
): EChartsOption {
  const definition = metricDefinition(metric)
  const ink = dark ? INK.dark : INK.light
  const rule = dark ? RULE.dark : RULE.light
  const named = chart.bars.length > 0
  // A legend for one series would name what the heading above the chart
  // already says. Two or more, and identity must not be colour alone.
  const legend = chart.bars.length > 1

  return {
    grid: { left: 48, right: 16, top: legend ? 16 : 24, bottom: 32 },
    ...(legend
      ? {
          legend: {
            data: chart.bars.map((bar) => bar.activity),
            top: 0,
            textStyle: { color: ink },
            icon: 'roundRect',
            itemHeight: 10,
            itemWidth: 14,
          },
        }
      : {}),
    tooltip: {
      trigger: 'axis',
      formatter: (parameters: unknown) => {
        const rows = parameters as { dataIndex: number }[]
        const index = rows[0]?.dataIndex ?? 0
        const point = chart.points[index]
        if (!point) return ''
        const total =
          point.value === null ? 'unavailable' : `${point.value.toFixed(1)} ${definition.axis}`
        const split = chart.bars
          .map((bar) => ({ bar, value: bar.values[index] }))
          .filter((entry) => entry.value !== null && entry.value !== undefined)
          .map((entry) => `${entry.bar.activity}: ${entry.value?.toFixed(1)} ${definition.axis}`)
        return [
          `<strong>${point.label}</strong>`,
          ...split,
          `${definition.label}: ${total}`,
          `${point.analysedTrackCount} of ${point.trackCount} analysed`,
        ].join('<br>')
      },
    },
    xAxis: {
      type: 'category',
      data: chart.labels,
      axisLabel: { color: ink },
      axisLine: { lineStyle: { color: rule } },
    },
    yAxis: {
      type: 'value',
      name: definition.axis,
      nameTextStyle: { color: ink },
      axisLabel: { color: ink },
      splitLine: { lineStyle: { color: rule } },
    },
    // A gap between neighbouring bars, so two activities in one bucket read as
    // two marks rather than as one striped block. The width is capped as well:
    // a chart of twelve months fills itself, and a chart of one year would
    // otherwise draw that year as a slab the width of the panel.
    barGap: '12%',
    barCategoryGap: '32%',
    series: named
      ? chart.bars.map((bar) => ({
          type: 'bar',
          name: bar.activity,
          data: bar.values,
          barMaxWidth: BAR_MAX_WIDTH,
          itemStyle: { color: activityColor(bar.activity, dark), borderRadius: [4, 4, 0, 0] },
        }))
      : [
          {
            type: 'bar',
            name: definition.label,
            data: chart.points.map((point) => point.value),
            barMaxWidth: BAR_MAX_WIDTH,
            itemStyle: { color: seriesColor(dark), borderRadius: [4, 4, 0, 0] },
          },
        ],
  }
}
