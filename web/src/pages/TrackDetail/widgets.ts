import type { Catalog, WidgetSizes, WidgetSpec, WidthClass } from '../../layout/document'
import type { Grid } from '../../layout/grid'

/**
 * The widgets a track's report is made of, and where they sit unasked.
 *
 * The *order* of the default is the order the report has always been read in:
 * the headline numbers, then where the track went and its shape, then how the
 * archive decided what it decided, and only then provenance and algorithms.
 * Rearranging is the owner's choice; the default stays an argument.
 *
 * What the report says is not up for arrangement. The title, the badges and
 * every notice about the analysis or the timing sit above the widgets and
 * cannot be hidden: a reader who hid "these durations are not verified" would
 * be reading a number with its caveat torn off. Map attribution is part of the
 * map widget for the same reason -- the map can move, its credit goes with it.
 */

type SizeTable = Readonly<Record<WidthClass, WidgetSizes>>

function sizes(
  wide: [width: number, height: number],
  medium: [width: number, height: number],
  narrow: [width: number, height: number],
  limits: { min: [number, number]; max: [number, number] },
): SizeTable {
  const table = (
    [width, height]: [number, number],
    columns: number,
    min: [number, number],
  ): WidgetSizes => ({
    size: { width, height },
    min: { width: Math.min(min[0], columns), height: min[1] },
    max: { width: Math.min(limits.max[0], columns), height: limits.max[1] },
  })
  return {
    wide: table(wide, 12, limits.min),
    medium: table(medium, 8, limits.min),
    // Two columns on a phone: a map half a phone wide is not a map, so every
    // panel spans both and only the small cards may sit side by side.
    narrow: table(narrow, 2, [limits.min[0] <= 2 ? 1 : 2, Math.min(limits.min[1], narrow[1])]),
  }
}

const METRIC = sizes([2, 2], [2, 2], [1, 2], { min: [2, 2], max: [6, 4] })
const MAP = sizes([6, 9], [8, 9], [2, 8], { min: [3, 6], max: [12, 20] })
const CHART = sizes([6, 9], [8, 9], [2, 9], { min: [4, 6], max: [12, 16] })
const WIDE_CHART = sizes([12, 7], [8, 7], [2, 7], { min: [4, 6], max: [12, 16] })
const PANEL = sizes([6, 8], [4, 8], [2, 8], { min: [3, 4], max: [12, 20] })
const WIDE_PANEL = sizes([12, 6], [8, 6], [2, 6], { min: [4, 4], max: [12, 20] })

function metric(id: string, label: string, description: string): WidgetSpec {
  return { id: `metric.${id}`, label, description, sizes: METRIC }
}

export const TRACK_WIDGETS: Catalog = [
  metric('distance', 'Distance', 'How far the track goes.'),
  metric('elevation-gain', 'Elevation gain', 'How much it climbs, from the filtered elevation.'),
  metric('moving', 'Moving time', 'Time spent going somewhere.'),
  metric('elapsed', 'Elapsed time', 'From the first position to the last.'),
  metric('moving-average', 'Moving average', 'Average speed while moving.'),
  metric('maximum-sustained', 'Maximum sustained', 'The fastest speed held for a while.'),
  {
    id: 'map',
    label: 'Where it went',
    description: 'The track on a map, with the basemap choice and its credits.',
    sizes: MAP,
  },
  {
    id: 'profile',
    label: 'Elevation and speed',
    description: 'Elevation and speed along the track, linked to the map.',
    sizes: CHART,
  },
  {
    id: 'sensors',
    label: 'Heart rate and cadence',
    description: 'What a heart-rate or cadence sensor measured, where the track has any.',
    sizes: WIDE_CHART,
  },
  {
    id: 'classification',
    label: 'What kind of track this is',
    description: 'Recorded, planned or unknown -- the evidence, and your correction.',
    sizes: PANEL,
  },
  {
    id: 'source',
    label: 'Where this track came from',
    description: 'Format, creator, positions, timeline and roughly where.',
    sizes: PANEL,
  },
  {
    id: 'derivation',
    label: 'How the numbers were derived',
    description: 'How the time divides, what was awkward about the data, and the algorithms.',
    sizes: WIDE_PANEL,
  },
]

/** The report as it has always been laid out, as a grid of twelve columns. */
export const DEFAULT_TRACK_LAYOUT: Grid = {
  columns: 12,
  tiles: [
    { widget: 'metric.distance', x: 0, y: 0, width: 2, height: 2 },
    { widget: 'metric.elevation-gain', x: 2, y: 0, width: 2, height: 2 },
    { widget: 'metric.moving', x: 4, y: 0, width: 2, height: 2 },
    { widget: 'metric.elapsed', x: 6, y: 0, width: 2, height: 2 },
    { widget: 'metric.moving-average', x: 8, y: 0, width: 2, height: 2 },
    { widget: 'metric.maximum-sustained', x: 10, y: 0, width: 2, height: 2 },
    { widget: 'map', x: 0, y: 2, width: 6, height: 9 },
    { widget: 'profile', x: 6, y: 2, width: 6, height: 9 },
    { widget: 'sensors', x: 0, y: 11, width: 12, height: 7 },
    { widget: 'classification', x: 0, y: 18, width: 6, height: 8 },
    { widget: 'source', x: 6, y: 18, width: 6, height: 8 },
    { widget: 'derivation', x: 0, y: 26, width: 12, height: 6 },
  ],
  hidden: [],
}
