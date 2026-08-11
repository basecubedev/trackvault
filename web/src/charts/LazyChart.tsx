import { Suspense, lazy } from 'react'
import type { HoverStore } from '../pages/TrackDetail/hover'
import type { EChartsOption } from './echarts'

/**
 * The chart, loaded when a page actually draws one.
 *
 * ECharts is the second-largest thing this application ships and only two of
 * the three pages draw a chart at all. Imported directly it lands in the entry
 * bundle, so opening the track list downloads a charting library to render a
 * list of rows.
 *
 * The fallback is a fixed-height block rather than a spinner, so the panel does
 * not resize under the reader when the chunk arrives.
 */
const Chart = lazy(async () => import('./Chart'))

export function LazyChart({
  option,
  height = 320,
  hover,
  label,
}: {
  option: EChartsOption
  height?: number
  hover?: HoverStore
  label: string
}) {
  return (
    <Suspense
      fallback={
        <div className="chart chart--pending" style={{ height }} role="status">
          <span className="muted">Loading chart…</span>
        </div>
      }
    >
      <Chart option={option} height={height} label={label} {...(hover ? { hover } : {})} />
    </Suspense>
  )
}
