import type { ProfileSample, TrackProfile } from '../../api/client'
import type { EChartsOption } from '../../charts/echarts'
import { seriesColor, sensorColor } from '../../charts/palette'

/**
 * The elevation and speed chart, as a plain function.
 *
 * Two decisions live here and both are contracts rather than cosmetics.
 *
 * **A hole stays a hole.** A sample whose speed could not be derived is `null`,
 * and `null` is what goes into the series. ECharts leaves a gap; a `0` would
 * draw a stop that never happened.
 *
 * **Segments never join.** A sample is emitted between two segments with every
 * value absent, so the line breaks where the recording did instead of running
 * across ground nobody travelled.
 */

export interface FlatSample {
  readonly sample: ProfileSample | null
  readonly km: number
}

export function flatten(profile: TrackProfile): FlatSample[] {
  const flat: FlatSample[] = []
  profile.segments.forEach((segment, index) => {
    if (index > 0) {
      const previous = flat[flat.length - 1]
      flat.push({ sample: null, km: previous ? previous.km : 0 })
    }
    for (const sample of segment.samples) {
      flat.push({ sample, km: sample.distance_m / 1000 })
    }
  })
  return flat
}

export interface ProfileOptions {
  readonly showRawElevation: boolean
  readonly showSpeed: boolean
  /** The surface it is drawn on. A canvas is outside the stylesheet's reach. */
  readonly dark: boolean
}

/** The ink axes and legends are written in. Text never wears a series colour. */
const INK = { light: '#5c636b', dark: '#a4acb4' }
const RULE = { light: '#d7dbe0', dark: '#333a41' }

export function profileOption(flat: readonly FlatSample[], options: ProfileOptions): EChartsOption {
  const ink = options.dark ? INK.dark : INK.light
  const rule = options.dark ? RULE.dark : RULE.light
  const axis = flat.map((entry) => entry.km.toFixed(3))
  const filtered = flat.map((entry) => entry.sample?.filtered_elevation_m ?? null)
  const raw = flat.map((entry) => entry.sample?.elevation_m ?? null)
  const speed = flat.map((entry) =>
    entry.sample?.speed_mps === null || entry.sample?.speed_mps === undefined
      ? null
      : entry.sample.speed_mps * 3.6,
  )

  const series: Record<string, unknown>[] = [
    {
      type: 'line',
      name: 'Elevation',
      data: filtered,
      yAxisIndex: 0,
      showSymbol: false,
      areaStyle: { opacity: 0.15 },
      lineStyle: { width: 2 },
      itemStyle: { color: seriesColor(options.dark) },
      connectNulls: false,
    },
  ]
  if (options.showRawElevation) {
    series.push({
      type: 'line',
      name: 'Elevation (raw)',
      data: raw,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { width: 1, type: 'dashed', opacity: 0.7 },
      itemStyle: { color: '#8a5a00' },
      connectNulls: false,
    })
  }
  if (options.showSpeed) {
    series.push({
      type: 'line',
      name: 'Speed',
      data: speed,
      yAxisIndex: 1,
      showSymbol: false,
      lineStyle: { width: 1.5 },
      itemStyle: { color: '#1a7f4b' },
      connectNulls: false,
    })
  }

  return {
    grid: { left: 62, right: 62, top: 36, bottom: 56 },
    legend: { top: 0, textStyle: { color: ink } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      // Written out rather than left to the default, because the default prints
      // two numbers with no units against an axis label at the edge of the
      // chart -- and a reader with the pointer in the middle of a climb should
      // not have to work out which of them is metres.
      formatter: (parameters: unknown) => {
        const rows = parameters as { dataIndex: number }[]
        const entry = flat[rows[0]?.dataIndex ?? 0]
        if (!entry?.sample) return ''
        const sample = entry.sample
        const lines = [
          `<strong>${entry.km.toFixed(2)} km</strong>`,
          `Elevation: ${describe(sample.filtered_elevation_m, (value) => `${Math.round(value)} m`)}`,
        ]
        if (options.showRawElevation) {
          lines.push(
            `Elevation (raw): ${describe(sample.elevation_m, (value) => `${Math.round(value)} m`)}`,
          )
        }
        if (options.showSpeed) {
          lines.push(
            `Speed: ${describe(sample.speed_mps, (value) => `${(value * 3.6).toFixed(1)} km/h`)}`,
          )
        }
        if (sample.time !== null) lines.push(`Time: ${new Date(sample.time).toISOString()}`)
        return lines.join('<br>')
      },
    },
    dataZoom: [
      { type: 'inside', throttle: 50 },
      { type: 'slider', height: 18, bottom: 8 },
    ],
    xAxis: {
      type: 'category',
      data: axis,
      name: 'Distance (km)',
      nameLocation: 'middle',
      nameGap: 28,
      axisLabel: { color: ink },
      axisLine: { lineStyle: { color: rule } },
    },
    yAxis: [
      {
        type: 'value',
        name: 'Elevation (m)',
        scale: true,
        nameTextStyle: { color: ink },
        axisLabel: { color: ink },
        splitLine: { lineStyle: { color: rule } },
      },
      {
        type: 'value',
        name: 'Speed (km/h)',
        scale: true,
        splitLine: { show: false },
        nameTextStyle: { color: ink },
        axisLabel: { color: ink },
      },
    ],
    series,
  }
}

/**
 * A value with its unit, or an honest dash.
 *
 * A sample the archive could not derive a speed for is `null`, and `0.0 km/h`
 * would draw a stop that never happened -- the same rule the whole interface
 * runs on, applied inside a tooltip where it is easiest to forget.
 */
function describe(value: number | null | undefined, render: (value: number) => string): string {
  return value === null || value === undefined ? 'unavailable' : render(value)
}

/** What a chart index points at, or `null` for the break between two segments. */
export function sampleAt(flat: readonly FlatSample[], index: number | null): ProfileSample | null {
  if (index === null || index < 0 || index >= flat.length) return null
  return flat[index]?.sample ?? null
}

/**
 * What the body did, on a chart of its own.
 *
 * Deliberately not a third line on the elevation chart. That one already
 * carries two scales, which is one more than a chart should have, and a heart
 * rate is neither a length nor a speed -- a third y-axis is how a reader ends
 * up comparing numbers that were never comparable.
 *
 * Beats per minute and revolutions per minute *are* comparable: both are counts
 * per minute, in overlapping ranges. So those two share one axis, and the axis
 * says what it is.
 *
 * Nothing is derived here. These are measurements passed through exactly as the
 * sensors reported them, holes included -- a strap losing contact is a gap in
 * the line, and a coasting bike really did report a cadence of zero.
 */
export function sensorOption(
  flat: readonly FlatSample[],
  { dark }: { dark: boolean },
): EChartsOption {
  const ink = dark ? '#a4acb4' : '#5c636b'
  const rule = dark ? '#333a41' : '#d7dbe0'
  const readings = [
    {
      name: 'Heart rate',
      colour: sensorColor('heart', dark),
      values: flat.map((entry) => entry.sample?.heart_rate_bpm ?? null),
    },
    {
      name: 'Cadence',
      colour: sensorColor('cadence', dark),
      values: flat.map((entry) => entry.sample?.cadence_rpm ?? null),
    },
  ].filter((reading) => reading.values.some((value) => value !== null))

  return {
    grid: { left: 56, right: 16, top: 36, bottom: 56 },
    legend: { top: 0, textStyle: { color: ink } },
    tooltip: {
      trigger: 'axis',
      formatter: (parameters: unknown) => {
        const rows = parameters as { dataIndex: number }[]
        const entry = flat[rows[0]?.dataIndex ?? 0]
        if (!entry?.sample) return ''
        return [
          `<strong>${entry.km.toFixed(2)} km</strong>`,
          `Heart rate: ${describe(entry.sample.heart_rate_bpm, (value) => `${value} bpm`)}`,
          `Cadence: ${describe(entry.sample.cadence_rpm, (value) => `${value} rpm`)}`,
        ].join('<br>')
      },
    },
    dataZoom: [
      { type: 'inside', throttle: 50 },
      { type: 'slider', height: 18, bottom: 8 },
    ],
    xAxis: {
      type: 'category',
      data: flat.map((entry) => entry.km.toFixed(3)),
      name: 'Distance (km)',
      nameLocation: 'middle',
      nameGap: 28,
      axisLabel: { color: ink },
      axisLine: { lineStyle: { color: rule } },
    },
    yAxis: {
      type: 'value',
      name: 'per minute',
      scale: true,
      nameTextStyle: { color: ink },
      axisLabel: { color: ink },
      splitLine: { lineStyle: { color: rule } },
    },
    series: readings.map((reading) => ({
      type: 'line',
      name: reading.name,
      data: reading.values,
      showSymbol: false,
      lineStyle: { width: 1.5 },
      itemStyle: { color: reading.colour },
      connectNulls: false,
    })),
  }
}

/**
 * Whether a track carries any sensor reading at all, and so has a chart to draw.
 *
 * Asked as "is there a value" rather than "is it not null": a segment break
 * carries no sample at all, and `undefined !== null` is true -- which would give
 * every track an empty sensor chart.
 */
export function hasSensorReadings(flat: readonly FlatSample[]): boolean {
  const present = (value: number | null | undefined): boolean =>
    value !== null && value !== undefined
  return flat.some(
    (entry) => present(entry.sample?.heart_rate_bpm) || present(entry.sample?.cadence_rpm),
  )
}
