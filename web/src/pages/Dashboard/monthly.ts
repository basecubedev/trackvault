import { shortMonthName } from '../../api/format'
import type { MonthlyStatistics } from '../../api/client'
import type { EChartsOption } from '../../charts/echarts'

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
  readonly month: number
  readonly label: string
  readonly value: number | null
  readonly trackCount: number
  readonly analysedTrackCount: number
}

/**
 * Twelve points, always.
 *
 * A month with nothing in it is present and empty rather than missing: a chart
 * that drops empty months redraws its own axis every time somebody imports a
 * file, and a year with one ride in July then looks like a year with one month.
 */
export function monthlySeries(
  statistics: MonthlyStatistics,
  metric: MonthlyMetric,
): MonthlyPoint[] {
  const read = metricDefinition(metric).read
  return statistics.months.map((bucket) => ({
    month: bucket.month,
    label: shortMonthName(bucket.month),
    value: read(bucket.totals),
    trackCount: bucket.totals.track_count,
    analysedTrackCount: bucket.totals.analysed_track_count,
  }))
}

export function monthlyOption(points: readonly MonthlyPoint[], metric: MonthlyMetric): EChartsOption {
  const definition = metricDefinition(metric)
  return {
    grid: { left: 48, right: 16, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (parameters: unknown) => {
        const rows = parameters as { dataIndex: number }[]
        const point = points[rows[0]?.dataIndex ?? 0]
        if (!point) return ''
        const value =
          point.value === null ? 'unavailable' : `${point.value.toFixed(1)} ${definition.axis}`
        const coverage = `${point.analysedTrackCount} of ${point.trackCount} analysed`
        return `<strong>${point.label}</strong><br>${definition.label}: ${value}<br>${point.trackCount} tracks<br>${coverage}`
      },
    },
    xAxis: { type: 'category', data: points.map((point) => point.label) },
    yAxis: { type: 'value', name: definition.axis },
    series: [
      {
        type: 'bar',
        name: definition.label,
        data: points.map((point) => point.value),
        itemStyle: { color: '#1c5d99' },
      },
    ],
  }
}
