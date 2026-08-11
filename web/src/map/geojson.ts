import type { Geometry, TrackProfile } from '../api/client'

/**
 * Turning the archive's geometry into what a map draws.
 *
 * Pure functions, deliberately. Every interesting decision about a map lives
 * here -- how segments become features, what a bounding box is, what happens at
 * the antimeridian -- and none of it needs WebGL to be checked. The map
 * component below is then a thin mount, which is the part a test cannot
 * meaningfully assert about anyway.
 */

export interface LineFeature {
  type: 'Feature'
  properties: { segment: number }
  geometry: { type: 'LineString'; coordinates: [number, number][] }
}

export interface FeatureCollection {
  type: 'FeatureCollection'
  features: LineFeature[]
}

export type Bounds = [[number, number], [number, number]]

export const SHAPE_PROJECTION_VERSION = 1
/**
 * How this module turns an archive's answer into a line and a frame.
 *
 * The archive says where a track went; the two functions below decide what is
 * actually drawn from that -- one feature per segment, where a line that
 * crosses 180° is cut, and which rectangle a map opens on including the padding
 * a single position gets. Every one of those changes the picture while the
 * archive's own data does not move an inch.
 *
 * So this belongs beside `BASEMAP_STYLE_VERSION` and `MINIMAP_RENDER_VERSION`
 * as a third thing somebody has to change on purpose. Three constants rather
 * than one, because each sits in the file whose edits require it: a version in
 * another module is a version nobody remembers.
 */

/**
 * One feature per segment. Never one feature for the track.
 *
 * A single line string across a segment boundary draws a straight line over
 * ground nobody travelled, and on a map that line is the most visible thing on
 * the screen.
 */
export function toFeatureCollection(geometry: Geometry): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: geometry.segments.map((segment, index) => ({
      type: 'Feature',
      properties: { segment: index },
      geometry: {
        type: 'LineString',
        coordinates: unwrapAntimeridian(
          segment.points.map((point) => [point.longitude, point.latitude] as [number, number]),
        ),
      },
    })),
  }
}

/** The same, from a profile, so the map can be drawn from the samples a chart uses. */
export function profileToFeatureCollection(profile: TrackProfile): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: profile.segments.map((segment) => ({
      type: 'Feature',
      properties: { segment: segment.index },
      geometry: {
        type: 'LineString',
        coordinates: unwrapAntimeridian(
          segment.samples.map((sample) => [sample.longitude, sample.latitude] as [number, number]),
        ),
      },
    })),
  }
}

/**
 * Keep a line that crosses 180° continuous.
 *
 * Longitude wraps and a map does not: a step from 179.9 to -179.9 is 0.2
 * degrees of ground and, drawn literally, a line all the way round the world.
 * Carrying the winding forward keeps the geometry continuous; the map projects
 * it back.
 */
export function unwrapAntimeridian(
  coordinates: readonly [number, number][],
): [number, number][] {
  const unwrapped: [number, number][] = []
  let offset = 0
  let previous: number | null = null
  for (const [longitude, latitude] of coordinates) {
    if (previous !== null) {
      const step = longitude + offset - previous
      if (step > 180) offset -= 360
      else if (step < -180) offset += 360
    }
    const shifted = longitude + offset
    unwrapped.push([shifted, latitude])
    previous = shifted
  }
  return unwrapped
}

/**
 * The box a map should open on.
 *
 * `null` when there is nothing to frame. A single position has no extent, so it
 * is padded into a small box rather than handed to a fitter that would divide
 * by zero and zoom to the atom.
 */
export function boundsOf(collection: FeatureCollection): Bounds | null {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const feature of collection.features) {
    for (const [longitude, latitude] of feature.geometry.coordinates) {
      west = Math.min(west, longitude)
      east = Math.max(east, longitude)
      south = Math.min(south, latitude)
      north = Math.max(north, latitude)
    }
  }
  if (!Number.isFinite(west) || !Number.isFinite(south)) return null
  const pad = 0.0005
  if (east - west < pad) {
    west -= pad
    east += pad
  }
  if (north - south < pad) {
    south -= pad
    north += pad
  }
  return [
    [west, south],
    [east, north],
  ]
}

/**
 * The rectangle a map asks the archive about, as the `bbox` query value.
 *
 * West, south, east, north -- the order the endpoint documents -- rounded to
 * three decimals, roughly a hundred metres. The rounding is what stops two
 * views that framed one track a pixel differently from asking the same question
 * twice, and it is why the answer can be reused across a page of rows.
 */
export function boundsQuery(bounds: Bounds): string {
  const [[west, south], [east, north]] = bounds
  return [west, south, east, north].map((value) => value.toFixed(3)).join(',')
}

/** A neutral background so the track is legible without any tile provider. */
export const FALLBACK_STYLE = {
  version: 8 as const,
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background' as const,
      paint: { 'background-color': '#e8edf1' },
    },
  ],
}
