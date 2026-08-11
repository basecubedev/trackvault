import type { MapCoverage, MapSource } from '../api/client'

/**
 * Turning installed coverage into a MapLibre style.
 *
 * The archive answers *which* packages belong behind a rectangle; this decides
 * what they look like. That split is deliberate: selection is a decision about
 * data and belongs to the backend, colour and line width are presentation and
 * belong here. A style document assembled in Python would put a design system
 * in an API layer.
 *
 * Every URL in the result is same-origin and relative -- tiles from this
 * archive, glyphs from this archive, no sprite at all. That is what makes the
 * offline contract hold: there is no host in this file to reach.
 *
 * **Outdoor, and not Topographic.** Shortbread carries no contours, no
 * hillshade and no elevation model. Calling the theme topographic would promise
 * something the data cannot deliver, so it promises what it does: paths,
 * tracks, cycleways and minor roads drawn as first-class features, with
 * everything muted enough that the track stays the brightest thing on screen.
 */

export type MapTheme = 'outdoor' | 'light' | 'none'

export const MAP_THEMES: readonly { id: MapTheme; label: string }[] = [
  { id: 'outdoor', label: 'Outdoor' },
  { id: 'light', label: 'Light' },
  { id: 'none', label: 'No basemap' },
]

/** The one font stack the glyph ranges in `public/fonts/` provide. */
const REGULAR = ['Noto Sans Regular']
const BOLD = ['Noto Sans Bold']

interface Palette {
  background: string
  water: string
  forest: string
  grass: string
  builtUp: string
  building: string
  majorRoad: string
  minorRoad: string
  path: string
  rail: string
  boundary: string
  label: string
  labelHalo: string
}

const PALETTES: Record<Exclude<MapTheme, 'none'>, Palette> = {
  // Muted greens and greys. Paths and tracks are the darkest lines on the map
  // after the track overlay itself, because on foot they are the map.
  outdoor: {
    background: '#f4f1ea',
    water: '#a8c8dc',
    forest: '#cfdfc4',
    grass: '#e2ead8',
    builtUp: '#eae5dc',
    building: '#ddd6ca',
    majorRoad: '#e8c9a0',
    minorRoad: '#ffffff',
    path: '#9a7b52',
    rail: '#b8b2a8',
    boundary: '#b0a89c',
    label: '#4a4438',
    labelHalo: '#f7f5f0',
  },
  // Neutral and pale. For reading a track's shape against as little as possible.
  light: {
    background: '#f7f8f9',
    water: '#cfe0ea',
    forest: '#e6ecdf',
    grass: '#eef2e8',
    builtUp: '#f0f0ef',
    building: '#e4e4e2',
    majorRoad: '#ffffff',
    minorRoad: '#ffffff',
    path: '#cfcfcb',
    rail: '#dcdcd8',
    boundary: '#cccac4',
    label: '#5c5c5c',
    labelHalo: '#ffffff',
  },
}

/** Anything a MapLibre style may hold. Kept loose on purpose -- see below. */
type StyleLayer = Record<string, unknown>

export interface BasemapStyle {
  version: 8
  glyphs?: string
  sources: Record<string, unknown>
  layers: StyleLayer[]
}

/**
 * Return the style for one theme over one set of installed packages.
 *
 * With no sources, or with the theme set to `none`, the result is the neutral
 * background: the track still draws, over nothing. That is a normal state and
 * not a degraded one.
 */
export function basemapStyle(coverage: MapCoverage | null, theme: MapTheme): BasemapStyle {
  const sources = theme === 'none' ? [] : (coverage?.sources ?? [])
  if (sources.length === 0) {
    return {
      version: 8,
      sources: {},
      layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e8edf1' } }],
    }
  }

  const palette = PALETTES[theme === 'none' ? 'light' : theme]
  const style: BasemapStyle = {
    version: 8,
    ...(coverage ? { glyphs: coverage.glyphs_url } : {}),
    sources: {},
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': palette.background } },
    ],
  }

  // Most specific last, so it draws on top. The backend orders them the other
  // way round because "most specific first" is how a reader would list them.
  const stacked = [...sources].reverse()
  for (const source of stacked) {
    style.sources[source.source_id] = vectorSource(source)
  }
  // Fills first for every source, then lines, then labels. Interleaving them
  // per source would put one region's roads under another region's forest.
  for (const source of stacked) style.layers.push(...areaLayers(source, palette))
  for (const source of stacked) style.layers.push(...lineLayers(source, palette))
  for (const source of stacked) style.layers.push(...labelLayers(source, palette))
  return style
}

function vectorSource(source: MapSource): Record<string, unknown> {
  return {
    type: 'vector',
    tiles: [absolute(source.tiles_url)],
    minzoom: source.min_zoom,
    maxzoom: source.max_zoom,
    // MapLibre will not request a tile outside these, so a package's own extent
    // is what stops the map asking this archive for tiles it does not hold.
    bounds: [
      source.bounds.min_longitude,
      source.bounds.min_latitude,
      source.bounds.max_longitude,
      source.bounds.max_latitude,
    ],
    // Rendered rather than left to MapLibre's default: the required text comes
    // from the package's own metadata, and it is never allowed to be absent.
    attribution: source.attribution.required_text,
  }
}

/**
 * Return a tile template MapLibre can resolve.
 *
 * It insists on an absolute URL for a tile template, so the relative path the
 * archive answers with is resolved against the page's own origin -- which is
 * the archive. Nothing here can produce a different host.
 */
function absolute(template: string): string {
  return `${window.location.origin}${template}`
}

function areaLayers(source: MapSource, palette: Palette): StyleLayer[] {
  const id = source.source_id
  return [
    {
      id: `${id}-ocean`,
      type: 'fill',
      source: id,
      'source-layer': 'ocean',
      paint: { 'fill-color': palette.water },
    },
    {
      id: `${id}-land`,
      type: 'fill',
      source: id,
      'source-layer': 'land',
      filter: ['in', ['get', 'kind'], ['literal', ['forest', 'wood', 'scrub', 'orchard']]],
      paint: { 'fill-color': palette.forest },
    },
    {
      id: `${id}-green`,
      type: 'fill',
      source: id,
      'source-layer': 'land',
      filter: [
        'in',
        ['get', 'kind'],
        ['literal', ['grass', 'meadow', 'park', 'garden', 'heath', 'grassland', 'farmland']],
      ],
      paint: { 'fill-color': palette.grass },
    },
    {
      id: `${id}-built`,
      type: 'fill',
      source: id,
      'source-layer': 'land',
      filter: ['in', ['get', 'kind'], ['literal', ['residential', 'industrial', 'commercial']]],
      paint: { 'fill-color': palette.builtUp },
    },
    {
      id: `${id}-water`,
      type: 'fill',
      source: id,
      'source-layer': 'water_polygons',
      paint: { 'fill-color': palette.water },
    },
    {
      id: `${id}-buildings`,
      type: 'fill',
      source: id,
      'source-layer': 'buildings',
      minzoom: 15,
      paint: { 'fill-color': palette.building, 'fill-opacity': 0.7 },
    },
  ]
}

function lineLayers(source: MapSource, palette: Palette): StyleLayer[] {
  const id = source.source_id
  return [
    {
      id: `${id}-waterways`,
      type: 'line',
      source: id,
      'source-layer': 'water_lines',
      paint: { 'line-color': palette.water, 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.6, 16, 3] },
    },
    {
      id: `${id}-boundaries`,
      type: 'line',
      source: id,
      'source-layer': 'boundaries',
      filter: ['<=', ['get', 'admin_level'], 4],
      paint: {
        'line-color': palette.boundary,
        'line-width': 1,
        'line-dasharray': [3, 2],
      },
    },
    {
      id: `${id}-rail`,
      type: 'line',
      source: id,
      'source-layer': 'streets',
      filter: ['==', ['get', 'rail'], true],
      minzoom: 10,
      paint: { 'line-color': palette.rail, 'line-width': 1 },
    },
    // Paths before roads, so a road crossing draws over a track rather than a
    // footway interrupting a motorway. Both are visible; the question is only
    // which one wins a junction.
    {
      id: `${id}-paths`,
      type: 'line',
      source: id,
      'source-layer': 'streets',
      filter: [
        'in',
        ['get', 'kind'],
        ['literal', ['path', 'footway', 'track', 'cycleway', 'bridleway', 'steps']],
      ],
      minzoom: 11,
      paint: {
        'line-color': palette.path,
        'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.6, 14, 1.2, 17, 2.4],
        'line-dasharray': [2, 1.5],
      },
      layout: { 'line-cap': 'round' },
    },
    {
      id: `${id}-minor-roads`,
      type: 'line',
      source: id,
      'source-layer': 'streets',
      filter: [
        'in',
        ['get', 'kind'],
        ['literal', ['residential', 'living_street', 'unclassified', 'service', 'pedestrian']],
      ],
      minzoom: 12,
      paint: {
        'line-color': palette.minorRoad,
        'line-width': ['interpolate', ['linear'], ['zoom'], 12, 0.5, 16, 4],
      },
    },
    {
      id: `${id}-major-roads`,
      type: 'line',
      source: id,
      'source-layer': 'streets',
      filter: [
        'in',
        ['get', 'kind'],
        ['literal', ['motorway', 'trunk', 'primary', 'secondary', 'tertiary']],
      ],
      paint: {
        'line-color': palette.majorRoad,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.6, 12, 2, 16, 8],
      },
      layout: { 'line-cap': 'round', 'line-join': 'round' },
    },
  ]
}

function labelLayers(source: MapSource, palette: Palette): StyleLayer[] {
  const id = source.source_id
  const halo = { 'text-halo-color': palette.labelHalo, 'text-halo-width': 1.4 }
  return [
    {
      id: `${id}-street-labels`,
      type: 'symbol',
      source: id,
      'source-layer': 'street_labels',
      minzoom: 14,
      layout: {
        'text-field': ['coalesce', ['get', 'name'], ''],
        'text-font': REGULAR,
        'text-size': 11,
        'symbol-placement': 'line',
      },
      paint: { 'text-color': palette.label, ...halo },
    },
    {
      id: `${id}-place-labels`,
      type: 'symbol',
      source: id,
      'source-layer': 'place_labels',
      layout: {
        'text-field': ['coalesce', ['get', 'name'], ''],
        'text-font': BOLD,
        'text-size': ['interpolate', ['linear'], ['zoom'], 4, 10, 12, 15],
        'text-max-width': 8,
      },
      paint: { 'text-color': palette.label, ...halo },
    },
  ]
}

/**
 * Return the attribution lines a set of sources requires, deduplicated.
 *
 * Deduplicated because two packages from one provider say the same thing, and a
 * control repeating "© OpenStreetMap contributors" twice reads as a defect
 * rather than as thoroughness.
 */
export function requiredAttribution(coverage: MapCoverage | null): string[] {
  const seen = new Set<string>()
  for (const source of coverage?.sources ?? []) {
    seen.add(source.attribution.required_text)
  }
  return [...seen]
}

/**
 * Add what one more map owes to what a page already credits.
 *
 * A page that draws many small maps learns what it has to credit one map at a
 * time, and the same package is usually behind most of them. Adding a line that
 * is already there must therefore be free -- so this returns the list it was
 * given, unchanged and by identity, whenever nothing is new. The caller keeps
 * this in browser state, and an equal-but-new array there is a re-render per
 * drawn map for a credit line that did not change.
 */
export function mergeAttribution(
  credited: readonly string[],
  lines: readonly string[],
): readonly string[] {
  const fresh = [...new Set(lines)].filter((line) => !credited.includes(line))
  return fresh.length === 0 ? credited : [...credited, ...fresh]
}
