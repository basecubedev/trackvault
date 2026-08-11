import { describe, expect, it } from 'vitest'
import type { ProfileSample, TrackProfile } from '../../api/client'
import type { EChartsOption } from '../../charts/echarts'
import { flatten, hasSensorReadings, profileOption, sampleAt, sensorOption } from './profileChart'

function sample(overrides: Partial<ProfileSample> = {}) {
  return {
    segment_index: 0,
    point_index: 0,
    distance_m: 0,
    latitude: 51,
    longitude: 8,
    time: null,
    elevation_m: 100,
    filtered_elevation_m: 100,
    speed_mps: 1.4,
    heart_rate_bpm: null,
    cadence_rpm: null,
    ...overrides,
  }
}

function profile(segments: ProfileSample[][]): TrackProfile {
  return {
    track_id: 1,
    sample_count: segments.flat().length,
    total_sample_count: segments.flat().length,
    total_distance_m: 1000,
    analysis_status: 'current',
    derived_with: {
      distance_algorithm: 'haversine',
      distance_algorithm_version: 1,
      movement_algorithm: 'windowed-extent',
      movement_algorithm_version: 2,
      elevation_algorithm: 'median-hysteresis',
      elevation_algorithm_version: 1,
      metric_schema_version: 2,
    },
    timing_basis: 'observed',
    is_actual_activity_timing: true,
    segments: segments.map((samples, index) => ({ index, samples })),
  }
}

describe('the chart series', () => {
  it('breaks between segments rather than drawing across the gap', () => {
    const flat = flatten(
      profile([
        [sample({ segment_index: 0, point_index: 0 }), sample({ segment_index: 0, point_index: 1 })],
        [sample({ segment_index: 1, point_index: 0, distance_m: 5000 })],
      ]),
    )

    expect(flat).toHaveLength(4)
    expect(flat[2]?.sample).toBeNull()
    const option = profileOption(flat, { showRawElevation: false, showSpeed: true, dark: false })
    const series = (option as { series: { data: (number | null)[] }[] }).series
    expect(series[0]?.data[2]).toBeNull()
  })

  it('keeps an underivable speed absent rather than drawing it as a stop', () => {
    const flat = flatten(
      profile([
        [
          sample({ segment_index: 0, point_index: 0, speed_mps: 1.4 }),
          sample({ segment_index: 0, point_index: 1, speed_mps: null }),
        ],
      ]),
    )
    const option = profileOption(flat, { showRawElevation: false, showSpeed: true, dark: false })
    const series = (option as { series: { name: string; data: (number | null)[] }[] }).series
    const speed = series.find((entry) => entry.name === 'Speed')

    expect(speed?.data[1]).toBeNull()
    expect(speed?.data[0]).toBeCloseTo(5.04, 2)
  })

  it('shows the filtered elevation by default and the raw one only on request', () => {
    const flat = flatten(profile([[sample({ segment_index: 0, point_index: 0 })]]))

    const filtered = profileOption(flat, { showRawElevation: false, showSpeed: false, dark: false })
    const both = profileOption(flat, { showRawElevation: true, showSpeed: false, dark: false })

    expect((filtered as { series: unknown[] }).series).toHaveLength(1)
    expect((both as { series: { name: string }[] }).series.map((entry) => entry.name)).toEqual([
      'Elevation',
      'Elevation (raw)',
    ])
  })
})

describe('sample identity', () => {
  it('resolves a chart index to the position it belongs to', () => {
    const flat = flatten(
      profile([
        [sample({ segment_index: 0, point_index: 0 }), sample({ segment_index: 0, point_index: 7 })],
      ]),
    )

    expect(sampleAt(flat, 1)?.point_index).toBe(7)
    expect(sampleAt(flat, null)).toBeNull()
    expect(sampleAt(flat, 99)).toBeNull()
  })
})

/**
 * What a body did, drawn beside what the ground did.
 *
 * A separate chart rather than a third line on the elevation one: heart rate is
 * not a length and not a speed, and a third y-scale on one chart is how a
 * reader ends up comparing two numbers that were never comparable. Beats per
 * minute and revolutions per minute *are* comparable -- both are counts per
 * minute, in overlapping ranges -- so those two share one axis.
 */
describe('the sensor chart', () => {
  const withSensors = [
    { km: 0, sample: sample({ heart_rate_bpm: 112, cadence_rpm: 78 }) },
    { km: 0.1, sample: sample({ heart_rate_bpm: null, cadence_rpm: 85 }) },
    { km: 0.2, sample: sample({ heart_rate_bpm: 141, cadence_rpm: 0 }) },
  ]

  it('draws a line for each sensor that measured anything', () => {
    const option = sensorOption(withSensors, { dark: false })

    expect(seriesNames(option)).toEqual(['Heart rate', 'Cadence'])
  })

  it('leaves a reading the sensor missed as a hole rather than a zero', () => {
    const option = sensorOption(withSensors, { dark: false })

    expect(dataOf(option, 0)).toEqual([112, null, 141])
  })

  it('keeps a measured zero, because a coasting bike is not a missing sensor', () => {
    const option = sensorOption(withSensors, { dark: false })

    expect(dataOf(option, 1)).toEqual([78, 85, 0])
  })

  it('offers no line for a sensor that was never there', () => {
    const heartOnly = [{ km: 0, sample: sample({ heart_rate_bpm: 112, cadence_rpm: null }) }]

    const option = sensorOption(heartOnly, { dark: false })

    expect(seriesNames(option)).toEqual(['Heart rate'])
  })

  it('says whether a track carries any reading at all', () => {
    expect(hasSensorReadings(withSensors)).toBe(true)
    expect(hasSensorReadings([{ km: 0, sample: sample({}) }])).toBe(false)
    expect(hasSensorReadings([{ km: 0, sample: null }])).toBe(false)
  })

  it('is drawn in steps chosen for a dark surface rather than the light ones', () => {
    const light = sensorOption(withSensors, { dark: false })
    const dark = sensorOption(withSensors, { dark: true })

    expect(colorOf(dark, 0)).not.toBe(colorOf(light, 0))
  })
})

function seriesNames(option: EChartsOption): string[] {
  return ((option as { series?: { name: string }[] }).series ?? []).map((entry) => entry.name)
}

function dataOf(option: EChartsOption, index: number): unknown {
  return ((option as { series?: { data: unknown }[] }).series ?? [])[index]?.data
}

function colorOf(option: EChartsOption, index: number): unknown {
  return ((option as { series?: { itemStyle?: { color?: string } }[] }).series ?? [])[index]
    ?.itemStyle?.color
}
