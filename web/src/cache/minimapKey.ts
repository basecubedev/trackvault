import type { MapCoverage } from '../api/client'
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
 * looks is unchanged. This module is that "everything", written out: the shape,
 * the maps drawn under it, the theme, the two version numbers standing for the
 * code that draws it, and every measurement the camera was given.
 *
 * The key is the canonical string itself rather than a digest of it. Hashing
 * would need `crypto.subtle`, which browsers withhold outside a secure context
 * -- and a self-hosted archive on `http://192.168.1.10:8080` is exactly that
 * case, so the cache would quietly stop working on the deployment this project
 * is built for. A few hundred bytes is a perfectly good IndexedDB key, and it
 * is one somebody can read in a debugger and understand.
 */

export const MINIMAP_CACHE_NAMESPACE = 'trackvault-minimap:v1'
/**
 * The version of what a cache entry *means*, carried in every key.
 *
 * Not the storage schema -- that is the database's own version. This changes
 * when the identity below stops saying the same thing, at which point every old
 * key is simply a key nothing asks for any more.
 */

export interface MinimapIdentity {
  /**
   * The archive's identity for the geometry being drawn: `geometry_sha256`.
   *
   * Read from the track rather than computed here. Reprocessing that moves a
   * position changes it, and a second hash invented in a browser would be a
   * second answer to a question the archive already answers.
   */
  geometry: string
  /**
   * How many segments the drawn shape has.
   *
   * Beside the geometry identity, not instead of it. `geometry_sha256` names
   * the *recording* -- positions and instants -- and deliberately leaves out
   * where the source put its breaks, because a route export of a paused ride is
   * the same afternoon. A minimap draws one line per segment, so the same
   * recording flattened into one run and split into three are two different
   * pictures. What survives is narrow and cosmetic: two exports of one
   * recording split differently into the *same* number of runs.
   */
  segments: number
  /** The packages drawn under the track, in the order coverage reported them. */
  deliveries: readonly string[]
  theme: MapTheme
  styleVersion: number
  renderVersion: number
  width: number
  height: number
  /** The position budget the shape was requested with. */
  points: number
  maxZoom: number
  padding: number
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
    `geometry=${identity.geometry}`,
    `segments=${String(identity.segments)}`,
    `maps=${identity.deliveries.join(',')}`,
    `theme=${identity.theme}`,
    `style=${String(identity.styleVersion)}`,
    `render=${String(identity.renderVersion)}`,
    `width=${String(identity.width)}`,
    `height=${String(identity.height)}`,
    `points=${String(identity.points)}`,
    `maxzoom=${String(identity.maxZoom)}`,
    `padding=${String(identity.padding)}`,
  ].join('|')
}

/**
 * Fill in everything about a minimap that is the same for every row.
 *
 * The measurements and the two version numbers are owned by the modules that
 * draw with them, and are read from there rather than restated. A caller
 * supplies only what differs per track, so no caller can leave a rendering
 * parameter out of the identity by forgetting it existed.
 */
export function minimapIdentity({
  geometry,
  segments,
  deliveries,
  theme,
}: {
  geometry: string
  segments: number
  deliveries: readonly string[]
  theme: MapTheme
}): MinimapIdentity {
  return {
    geometry,
    segments,
    deliveries,
    theme,
    styleVersion: BASEMAP_STYLE_VERSION,
    renderVersion: MINIMAP_RENDER_VERSION,
    width: MINIMAP_WIDTH,
    height: MINIMAP_HEIGHT,
    points: MINIMAP_POINTS,
    maxZoom: MINIMAP_MAX_ZOOM,
    padding: MINIMAP_PADDING,
  }
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
