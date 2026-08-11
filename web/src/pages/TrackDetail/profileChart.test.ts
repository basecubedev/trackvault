import { describe, expect, it } from 'vitest'
import type { ProfileSample, TrackProfile } from '../../api/client'
import { flatten, profileOption, sampleAt } from './profileChart'

function sample(overrides: Partial<ProfileSample> & { segment_index: number; point_index: number }) {
  return {
    distance_m: 0,
    latitude: 51,
    longitude: 8,
    time: null,
    elevation_m: 100,
    filtered_elevation_m: 100,
    speed_mps: 1.4,
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
    const option = profileOption(flat, { showRawElevation: false, showSpeed: true })
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
    const option = profileOption(flat, { showRawElevation: false, showSpeed: true })
    const series = (option as { series: { name: string; data: (number | null)[] }[] }).series
    const speed = series.find((entry) => entry.name === 'Speed')

    expect(speed?.data[1]).toBeNull()
    expect(speed?.data[0]).toBeCloseTo(5.04, 2)
  })

  it('shows the filtered elevation by default and the raw one only on request', () => {
    const flat = flatten(profile([[sample({ segment_index: 0, point_index: 0 })]]))

    const filtered = profileOption(flat, { showRawElevation: false, showSpeed: false })
    const both = profileOption(flat, { showRawElevation: true, showSpeed: false })

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
