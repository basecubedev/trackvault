import { type MinimapCache, minimapCache } from './minimapCache'

/**
 * Getting one drawn minimap: from what is kept, or by drawing it once.
 *
 * Two jobs that look like one. The first is the cache lookup, which is the
 * point of the exercise: a picture that already exists must not be drawn again.
 * The second is that *concurrent* requests for the same picture must not each
 * draw it either -- and that case is not hypothetical. A page of rows mounts
 * twenty-five previews at once, React mounts a component twice in development,
 * and one recording imported from two files is two rows drawing the same
 * afternoon over the same map. Without this, every one of them is a WebGL
 * context, and a browser grants a page around sixteen.
 *
 * The register is deliberately process-local and deliberately not the cache:
 * it holds work in flight, not results, and it is empty again the moment the
 * work settles.
 */

const drawing = new Map<string, Promise<Blob>>()

/**
 * Return the rendering for one cache key, drawing it only if nothing has it.
 *
 * `draw` is called at most once per key at a time, and not at all on a hit.
 * Nothing the cache does can prevent a picture from being produced: a store
 * that cannot be read, cannot be written or throws outright ends with the map
 * drawn the ordinary way.
 */
export function renderedMinimap(
  key: string,
  draw: () => Promise<Blob>,
  cache: MinimapCache = minimapCache,
): Promise<Blob> {
  const started = drawing.get(key)
  if (started !== undefined) return started
  const work = reuseOrDraw(key, draw, cache).finally(() => {
    drawing.delete(key)
  })
  drawing.set(key, work)
  return work
}

async function reuseOrDraw(
  key: string,
  draw: () => Promise<Blob>,
  cache: MinimapCache,
): Promise<Blob> {
  // Before the expensive part, always. A lookup after the render would be a
  // cache that costs what it exists to save.
  let kept: Blob | null = null
  try {
    kept = await cache.get(key)
  } catch {
    kept = null
  }
  if (kept !== null) return kept
  const drawn = await draw()
  try {
    await cache.put(key, drawn)
  } catch {
    /* drawn is what the row needs; keeping it is next time's saving */
  }
  return drawn
}
