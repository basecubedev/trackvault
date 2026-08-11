import type { MapCoverage } from '../api/client'
import { SHAPE_PROJECTION_VERSION } from '../map/geojson'
import {
  MINIMAP_HEIGHT,
  MINIMAP_MAX_ZOOM,
  MINIMAP_PADDING,
  MINIMAP_POINTS,
  MINIMAP_RENDER_VERSION,
  MINIMAP_WIDTH,
} from '../map/minimap'
import { BASEMAP_STYLE_VERSION, type MapTheme } from '../map/style'

/**
 * What makes two drawn minimaps the same picture.
 *
 * A rendering may be reused when, and only when, everything that decided how it
 * looks is unchanged -- and it *must* be reused when all of it is. This module
 * is that "everything", written out: the line, the maps drawn under it, the
 * theme, the three version numbers standing for the code that draws it, every
 * measurement the camera is given, and the resolution it draws at.
 *
 * The key is the canonical string itself rather than a digest of it. Hashing
 * would need `crypto.subtle`, which browsers withhold outside a secure context
 * -- and a self-hosted archive on `http://192.168.1.10:8080` is exactly that
 * case, so the cache would quietly stop working on the deployment this project
 * is built for. A few hundred bytes is a perfectly good IndexedDB key, and it
 * is one somebody can read in a debugger and understand.
 */

export const MINIMAP_CACHE_NAMESPACE = 'trackvault-minimap:v2'
/**
 * The version of what a cache entry *means*, carried in every key.
 *
 * Not the storage schema -- that is the database's own version. This changes
 * when the identity below stops saying the same thing, at which point every old
 * key is a key nothing asks for any more.
 *
 * `v1` keyed a picture on the track's *recording* identity plus a segment
 * count. That is not the identity of a drawing: the same positions split into
 * two runs a position apart are one recording and two pictures, and `v1` could
 * not tell them apart. Nothing written under it can be trusted to be a picture
 * of what its key says, so none of it is reused.
 */

export interface MinimapIdentity {
  /**
   * The archive's identity for the line being drawn: `shape_sha256`.
   *
   * Read from the geometry response rather than computed here, and it is the
   * identity of *that response* -- so the positions, the order they are drawn
   * in, where the segments begin and end, and the reduction that produced them
   * are all in one value. A second hash invented in a browser would be a second
   * answer to a question the archive already answers.
   */
  shape: string
  /** The packages drawn under the track, in the order coverage reported them. */
  deliveries: readonly string[]
  theme: MapTheme
  /** The version of the code that turns an archive answer into a line and a frame. */
  projectionVersion: number
  /** The version of the code that decides what a basemap looks like. */
  styleVersion: number
  /** The version of the code that drives the camera and draws the track over it. */
  renderVersion: number
  width: number
  height: number
  /** The position budget the shape was requested with. */
  points: number
  maxZoom: number
  padding: number
  /**
   * The display resolution the picture is drawn at.
   *
   * MapLibre sizes its canvas at the device pixel ratio, so the same track on a
   * plain monitor and on a high-resolution laptop are a 132x96 image and a
   * 264x192 one. Both are shown in the same hundred-pixel box and they are not
   * the same picture; without this, moving a window between two screens shows
   * one of them a blurred copy of the other.
   */
  pixelRatio: number
}

/**
 * Return the cache key for one rendering.
 *
 * Every field is named in the result. A positional join would be a key whose
 * meaning depends on an argument order, and the day a field is inserted in the
 * middle every old entry silently becomes a wrong answer rather than a miss.
 */
export function minimapCacheKey(identity: MinimapIdentity): string {
  return [
    MINIMAP_CACHE_NAMESPACE,
    `shape=${identity.shape}`,
    `maps=${identity.deliveries.join(',')}`,
    `theme=${identity.theme}`,
    `projection=${String(identity.projectionVersion)}`,
    `style=${String(identity.styleVersion)}`,
    `render=${String(identity.renderVersion)}`,
    `width=${String(identity.width)}`,
    `height=${String(identity.height)}`,
    `points=${String(identity.points)}`,
    `maxzoom=${String(identity.maxZoom)}`,
    `padding=${String(identity.padding)}`,
    `pixel=${String(identity.pixelRatio)}`,
  ].join('|')
}

/**
 * Fill in everything about a minimap that a caller does not decide per track.
 *
 * The measurements and the three version numbers are owned by the modules that
 * draw with them, and are read from there rather than restated. A caller
 * supplies only what differs per track, so no row can leave a rendering
 * parameter out of the identity by forgetting it existed.
 *
 * `points` is kept although the shape identity already reflects it: that
 * identity names the *answer* and this names the *request*, and a key that says
 * which budget produced a shape is one somebody can read.
 */
export function minimapIdentity({
  shape,
  deliveries,
  theme,
}: {
  shape: string
  deliveries: readonly string[]
  theme: MapTheme
}): MinimapIdentity {
  return {
    shape,
    deliveries,
    theme,
    projectionVersion: SHAPE_PROJECTION_VERSION,
    styleVersion: BASEMAP_STYLE_VERSION,
    renderVersion: MINIMAP_RENDER_VERSION,
    width: MINIMAP_WIDTH,
    height: MINIMAP_HEIGHT,
    points: MINIMAP_POINTS,
    maxZoom: MINIMAP_MAX_ZOOM,
    padding: MINIMAP_PADDING,
    pixelRatio: displayPixelRatio(),
  }
}

/**
 * Return the resolution a canvas will be drawn at.
 *
 * Read at the moment a picture is asked for rather than once at start-up: a
 * window dragged to another screen, or a page zoomed, changes it while the list
 * is open. A browser that reports something unusable is treated as an ordinary
 * one, because a key is no place to discover a defect.
 */
function displayPixelRatio(): number {
  const reported = globalThis.devicePixelRatio
  return typeof reported === 'number' && reported > 0 && Number.isFinite(reported) ? reported : 1
}

/**
 * Return the packages a coverage answer would draw, by their delivery identity.
 *
 * The identity the archive states, in the order it stated it -- which is the
 * order the style stacks them in. A package replaced by a newer build of the
 * same region is a different content hash, so the picture drawn over the old
 * one is a picture of a map that is no longer installed, and it is not found
 * again rather than being hunted down and deleted.
 */
export function drawnDeliveries(coverage: MapCoverage | null): string[] {
  return (coverage?.sources ?? []).map((source) => source.delivery_id)
}
