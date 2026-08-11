import { describe, expect, it } from 'vitest'
import type { Geometry, TrackProfile } from '../api/client'
import { boundsOf, boundsQuery, toFeatureCollection, unwrapAntimeridian } from './geojson'

/**
 * Everything a map decides that does not need WebGL.
 *
 * The map component is a mount and asserting about it needs a GPU. What is
 * worth checking -- that segments stay apart, that a line across 180° is not
 * drawn round the world, that a single position does not divide by zero -- is
 * all here as plain functions.
 */

function geometry(segments: [number, number][][]): Geometry {
  return {
    track_id: 1,
    segment_count: segments.length,
    point_count: segments.reduce((total, points) => total + points.length, 0),
    total_point_count: segments.reduce((total, points) => total + points.length, 0),
    simplified: false,
    segments: segments.map((points) => ({
      points: points.map(([longitude, latitude]) => ({
        longitude,
        latitude,
        elevation: null,
        time: null,
      })),
    })),
  }
}

describe('segments are never joined', () => {
  it('emits one feature per segment', () => {
    const collection = toFeatureCollection(
      geometry([
        [
          [8, 51],
          [8.01, 51.01],
        ],
        [
          [9, 52],
          [9.01, 52.01],
        ],
      ]),
    )

    expect(collection.features).toHaveLength(2)
    expect(collection.features[0]?.geometry.coordinates).toHaveLength(2)
    expect(collection.features[1]?.geometry.coordinates[0]).toEqual([9, 52])
  })
})

describe('the antimeridian', () => {
  it('keeps a crossing continuous rather than drawing a line round the world', () => {
    const unwrapped = unwrapAntimeridian([
      [179.9, 0],
      [-179.9, 0],
      [-179.7, 0],
    ])

    expect(unwrapped[1]?.[0]).toBeCloseTo(180.1, 6)
    expect(unwrapped[2]?.[0]).toBeCloseTo(180.3, 6)
    const steps = unwrapped.slice(1).map((point, index) => point[0] - (unwrapped[index]?.[0] ?? 0))
    expect(Math.max(...steps.map(Math.abs))).toBeLessThan(1)
  })

  it('leaves an ordinary track alone', () => {
    const unwrapped = unwrapAntimeridian([
      [8, 51],
      [8.1, 51.1],
    ])
    expect(unwrapped).toEqual([
      [8, 51],
      [8.1, 51.1],
    ])
  })
})

describe('bounds', () => {
  it('frames a track', () => {
    const bounds = boundsOf(
      toFeatureCollection(
        geometry([
          [
            [8, 51],
            [9, 52],
          ],
        ]),
      ),
    )
    expect(bounds).not.toBeNull()
    expect(bounds?.[0]).toEqual([8, 51])
    expect(bounds?.[1]).toEqual([9, 52])
  })

  it('pads a single position instead of framing nothing', () => {
    const bounds = boundsOf(toFeatureCollection(geometry([[[8, 51]]])))

    expect(bounds).not.toBeNull()
    if (bounds === null) return
    const [[west, south], [east, north]] = bounds
    expect(east).toBeGreaterThan(west)
    expect(north).toBeGreaterThan(south)
  })

  it('has nothing to frame for an empty track', () => {
    expect(boundsOf({ type: 'FeatureCollection', features: [] })).toBeNull()
  })
})

describe('the rectangle coverage is asked about', () => {
  it('is west, south, east, north, in that order', () => {
    expect(
      boundsQuery([
        [2.8, 39.6],
        [2.9, 39.7],
      ]),
    ).toBe('2.800,39.600,2.900,39.700')
  })

  it('is rounded, so two views of one track ask the same question', () => {
    const asked = boundsQuery([
      [2.80001, 39.60002],
      [2.9, 39.7],
    ])
    const askedAgain = boundsQuery([
      [2.80002, 39.60001],
      [2.9, 39.7],
    ])

    expect(asked).toBe(askedAgain)
  })
})

describe('a profile draws the same shape as the geometry', () => {
  it('keeps segments apart there too', () => {
    const profile: TrackProfile = {
      track_id: 1,
      sample_count: 3,
      total_sample_count: 3,
      total_distance_m: 100,
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
      segments: [
        {
          index: 0,
          samples: [
            sample(0, 0, 8, 51),
            sample(0, 1, 8.01, 51.01),
          ],
        },
        { index: 1, samples: [sample(1, 0, 9, 52)] },
      ],
    }

    const collection = toFeatureCollection({
      track_id: 1,
      segment_count: 2,
      point_count: 3,
      total_point_count: 3,
      simplified: false,
      segments: profile.segments.map((segment) => ({
        points: segment.samples.map((entry) => ({
          latitude: entry.latitude,
          longitude: entry.longitude,
          elevation: entry.elevation_m,
          time: entry.time,
        })),
      })),
    })

    expect(collection.features).toHaveLength(2)
  })
})

function sample(segmentIndex: number, pointIndex: number, longitude: number, latitude: number) {
  return {
    segment_index: segmentIndex,
    point_index: pointIndex,
    distance_m: 0,
    latitude,
    longitude,
    time: null,
    elevation_m: null,
    filtered_elevation_m: null,
    speed_mps: null,
    heart_rate_bpm: null,
    cadence_rpm: null,
  }
}
