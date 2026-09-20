import type { Map as MapLibreMap, StyleSpecification } from 'maplibre-gl'
import type { Bounds, FeatureCollection } from './geojson'
import type { BasemapStyle, MapTheme } from './style'
import { pointAtBundledWorker } from './worker'

/**
 * Drawing one track small, once, and handing back a picture of it.
 *
 * A list row wants a map; it does not want *a map component*. Mounting one
 * MapLibre map per row would be the obvious implementation and it is the wrong
 * one: every instance holds a WebGL context, a browser grants a page somewhere
 * around sixteen of them, and the seventeenth costs the first its canvas. A
 * page of twenty-five rows would go blank in an order nobody can predict, on
 * large screens first.
 *
 * So the map is a means rather than the product. One map is built off screen,
 * asked to draw one track, photographed, and destroyed. What the row keeps is
 * an image: no context, no event listeners, no scroll-wheel that swallows the
 * page. Two of these run at a time, because a queue of one is slow and a queue
 * of everything is the problem this exists to avoid. Tiles arrive from the
 * browser's cache after the first row has paid for them -- the archive serves
 * them immutable under a content hash, which is exactly the case a cache is for.
 *
 * This module knows nothing about the picture being kept. `src/cache/` decides
 * whether one has to be drawn at all, and everything here that decides what the
 * result *looks like* is published as `MINIMAP_RENDER_VERSION` for it to read.
 *
 * Everything decidable without WebGL lives in `geojson.ts` and `style.ts` and
 * is tested there. This is the camera.
 */

export const MINIMAP_POINTS = 150
/**
 * How many positions a preview asks the archive for.
 *
 * Far below what a recording holds, and more than a hundred-pixel box can
 * distinguish. A list is exactly where asking for the canonical geometry would
 * be most expensive and least visible: twenty-five long walks are megabytes of
 * coordinates to draw something the size of a postage stamp.
 */

export const MINIMAP_WIDTH = 132
export const MINIMAP_HEIGHT = 96
/** The theme a preview draws in. Muted enough that the track is the subject. */
export const MINIMAP_THEME: MapTheme = 'outdoor'

export const MINIMAP_PADDING = 6
export const MINIMAP_MAX_ZOOM = 14

export const MINIMAP_RENDER_VERSION = 1
/**
 * What this module draws, as a number somebody has to change on purpose.
 *
 * Everything above is a value a cache key can read. This stands for everything
 * below that is not: the two track lines, their colours and widths, which
 * events the camera waits for. Change any of that and a picture drawn by the
 * previous build is no longer a picture this build would produce -- so bump
 * this in the same commit, and every kept rendering falls out of use by itself.
 *
 * Deliberately a constant rather than a hash of the source. A digest of this
 * file would change for a renamed local and stay the same for a colour moved
 * into `style.ts`, which is precisely backwards.
 */

const CONCURRENT_RENDERS = 2
const RENDER_TIMEOUT_MS = 15_000

export interface MinimapRequest {
  /** The track, one feature per segment, exactly as the detail map draws it. */
  collection: FeatureCollection
  /** The rectangle to frame. */
  bounds: Bounds
  /** The basemap, composed from what this archive says is installed. */
  style: BasemapStyle
}

let running = 0
const waiting: (() => void)[] = []

/**
 * Draw one track over one basemap and return the picture as a PNG.
 *
 * A `Blob` rather than a `data:` URL, because the picture now outlives the page
 * that drew it: bytes are what a store keeps, and base64 in a string would be a
 * third more of somebody's disk for a form nothing needs.
 *
 * Rejects when the style could not be loaded at all, or when the canvas could
 * not be read back. A basemap that is merely incomplete is not a failure: the
 * track is the subject, and a picture of it over a partly drawn map is worth
 * more than an empty box.
 */
export async function renderTrackMinimap({
  collection,
  bounds,
  style,
}: MinimapRequest): Promise<Blob> {
  await enterQueue()
  const frame = offscreenFrame()
  try {
    document.body.append(frame)
    // Imported here rather than at the top of the module: the track list is a
    // page of rows, and it should not download a rendering library before
    // anybody has scrolled to a map.
    const maplibregl = await import('maplibre-gl')
    pointAtBundledWorker(maplibregl)
    const map = new maplibregl.Map({
      container: frame,
      style: style as StyleSpecification,
      interactive: false,
      // The credit is not dropped, it is moved: a hundred-pixel box cannot
      // carry a legible attribution line, so the page that shows these images
      // carries one for all of them.
      attributionControl: false,
      fadeDuration: 0,
      bounds,
      fitBoundsOptions: { padding: MINIMAP_PADDING, maxZoom: MINIMAP_MAX_ZOOM, duration: 0 },
      // Without this the canvas is cleared before anything can read it back,
      // which is the whole point of building this map.
      canvasContextAttributes: { preserveDrawingBuffer: true },
    })
    try {
      // A package can be removed, or turn out to be damaged, while a list is
      // open. Losing it must cost the basemap and nothing else.
      map.on('error', () => {})
      if (!(await settled(map, 'load'))) {
        throw new Error('the basemap did not load')
      }
      drawTrack(map, collection)
      await settled(map, 'idle')
      return await photograph(map.getCanvas())
    } finally {
      map.remove()
    }
  } finally {
    frame.remove()
    leaveQueue()
  }
}

/**
 * Read the drawn canvas back as a PNG.
 *
 * `toBlob` hands back `null` when the browser could not encode the canvas at
 * all -- a lost WebGL context is the realistic way that happens. It is reported
 * rather than resolved as an empty picture, so nothing keeps a blank image
 * under a key that says it is a track.
 */
function photograph(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((image) => {
      if (image === null) reject(new Error('the drawn map could not be read back'))
      else resolve(image)
    }, 'image/png')
  })
}

/** The same two lines the detail map draws, so one track looks like itself. */
function drawTrack(map: MapLibreMap, collection: FeatureCollection): void {
  map.addSource('track', { type: 'geojson', data: collection })
  map.addLayer({
    id: 'track-casing',
    type: 'line',
    source: 'track',
    paint: { 'line-color': '#ffffff', 'line-width': 3.5, 'line-opacity': 0.8 },
    layout: { 'line-join': 'round', 'line-cap': 'round' },
  })
  map.addLayer({
    id: 'track-line',
    type: 'line',
    source: 'track',
    paint: { 'line-color': '#1c5d99', 'line-width': 1.8 },
    layout: { 'line-join': 'round', 'line-cap': 'round' },
  })
}

/**
 * Wait for one map event, and report whether it arrived.
 *
 * A deadline rather than an open wait: a package whose tiles cannot be read
 * would otherwise hold a queue slot for as long as the page is open, and every
 * row below it would wait for a map that is never coming.
 */
function settled(map: MapLibreMap, event: 'load' | 'idle'): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const arrived = () => {
      clearTimeout(deadline)
      map.off(event, arrived)
      resolve(true)
    }
    const deadline = setTimeout(() => {
      map.off(event, arrived)
      resolve(false)
    }, RENDER_TIMEOUT_MS)
    map.on(event, arrived)
  })
}

/**
 * A box the map can be built in without anybody seeing it.
 *
 * Off screen rather than hidden: MapLibre sizes its canvas from the element,
 * and an element with `display: none` has no size to read.
 */
function offscreenFrame(): HTMLDivElement {
  const frame = document.createElement('div')
  frame.setAttribute('aria-hidden', 'true')
  frame.style.position = 'absolute'
  frame.style.left = '-10000px'
  frame.style.top = '0'
  frame.style.width = `${MINIMAP_WIDTH}px`
  frame.style.height = `${MINIMAP_HEIGHT}px`
  frame.style.pointerEvents = 'none'
  return frame
}

async function enterQueue(): Promise<void> {
  if (running >= CONCURRENT_RENDERS) {
    await new Promise<void>((resolve) => waiting.push(resolve))
  }
  running += 1
}

function leaveQueue(): void {
  running -= 1
  waiting.shift()?.()
}
