import type * as MapLibre from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

/**
 * Telling MapLibre where its own worker is.
 *
 * MapLibre 6 works out the worker's address at run time, from `import.meta.url`
 * of its own module. A bundler cannot see through that: Vite emits no such file,
 * the built page asks for `/assets/maplibre-gl-worker.mjs`, and the archive
 * answers 404. The worker is not an optimisation -- it is what parses vector
 * tiles -- so without it no tile is ever requested at all.
 *
 * The failure that follows is the quiet kind. A worker that was never created
 * raises nothing, so no `error` event arrives and no message reaches the
 * console. The style loads, the canvas renders an empty basemap, and `load`
 * simply never fires, so everything waiting on it waits until its own deadline.
 * It is invisible in `npm run dev`, where the module is served from
 * `node_modules` and the computed address happens to resolve.
 *
 * `?worker&url` rather than `?url`, because the worker is not one file: it
 * imports `./maplibre-gl-shared.mjs` beside it. Copying the entry alone yields a
 * module whose own import resolves to nothing, which fails in the same silence
 * one step later. This bundles the worker together with what it imports and
 * hands `setWorkerUrl` the address of the result -- same origin, so the policy
 * in `src/trackvault/api/security.py` needs no exception for it.
 */
export function pointAtBundledWorker(maplibregl: typeof MapLibre): void {
  maplibregl.setWorkerUrl(workerUrl)
}
